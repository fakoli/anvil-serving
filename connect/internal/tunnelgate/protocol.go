package tunnelgate

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
)

const UpgradePath = "/acv1/events"

func one(h http.Header, key string) (string, bool) {
	values := h.Values(key)
	return h.Get(key), len(values) == 1 && values[0] != ""
}

// descriptor treats wstunnel's JWT solely as untrusted routing input. Its
// upstream signature is not an authentication mechanism. Only ReverseTcp for
// the already-authorized resource's exact loopback port is permitted.
func descriptor(value, address string) bool {
	const prefix = "v1, authorization.bearer."
	if len(value) > 2048 || !strings.HasPrefix(value, prefix) {
		return false
	}
	parts := strings.Split(strings.TrimPrefix(value, prefix), ".")
	if len(parts) != 3 {
		return false
	}
	decoded := make([][]byte, 3)
	for i, part := range parts {
		b, err := base64.RawURLEncoding.DecodeString(part)
		if err != nil || len(b) == 0 || base64.RawURLEncoding.EncodeToString(b) != part {
			return false
		}
		decoded[i] = b
	}
	header, ok := exactObject(decoded[0], "alg", "typ")
	if !ok || string(header["alg"]) != `"HS256"` || string(header["typ"]) != `"JWT"` || len(decoded[2]) != 32 {
		return false
	}
	claims, ok := exactObject(decoded[1], "id", "p", "r", "rp")
	if !ok || string(claims["p"]) != `"ReverseTcp"` || string(claims["r"]) != `"127.0.0.1"` {
		return false
	}
	var id string
	var port uint16
	if json.Unmarshal(claims["id"], &id) != nil || !uuid(id) || json.Unmarshal(claims["rp"], &port) != nil || port == 0 {
		return false
	}
	host, expected, err := net.SplitHostPort(address)
	return err == nil && host == "127.0.0.1" && expected == strconv.Itoa(int(port))
}

func uuid(value string) bool {
	if len(value) != 36 || value[8] != '-' || value[13] != '-' || value[18] != '-' || value[23] != '-' {
		return false
	}
	raw := strings.ReplaceAll(value, "-", "")
	b, err := hex.DecodeString(raw)
	return err == nil && len(b) == 16 && hex.EncodeToString(b) == raw
}

func exactObject(data []byte, keys ...string) (map[string]json.RawMessage, bool) {
	d := json.NewDecoder(bytes.NewReader(data))
	token, err := d.Token()
	if err != nil || token != json.Delim('{') {
		return nil, false
	}
	allowed := map[string]bool{}
	for _, key := range keys {
		allowed[key] = true
	}
	result := map[string]json.RawMessage{}
	for d.More() {
		token, err := d.Token()
		key, ok := token.(string)
		if err != nil || !ok || !allowed[key] || result[key] != nil {
			return nil, false
		}
		var value json.RawMessage
		if d.Decode(&value) != nil {
			return nil, false
		}
		result[key] = value
	}
	if token, err := d.Token(); err != nil || token != json.Delim('}') || len(result) != len(keys) {
		return nil, false
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, false
	}
	return result, true
}

func upgradeHead(r *http.Request, host string) (string, string, bool) {
	if r.Method != http.MethodGet || r.ProtoMajor != 1 || r.ProtoMinor != 1 || r.Host != host || r.URL == nil || r.URL.Path != UpgradePath || r.URL.RawPath != "" || r.URL.RawQuery != "" || r.URL.ForceQuery || r.URL.Fragment != "" || r.URL.IsAbs() || r.RequestURI != UpgradePath || r.ContentLength != 0 || len(r.TransferEncoding) != 0 || len(r.Trailer) != 0 {
		return "", "", false
	}
	for _, name := range []string{"Origin", "Cookie", "Proxy-Authorization", "X-Api-Key", "Transfer-Encoding", "Trailer", "Expect", "Sec-WebSocket-Extensions"} {
		if _, exists := r.Header[http.CanonicalHeaderKey(name)]; exists {
			return "", "", false
		}
	}
	if values, exists := r.Header["Content-Length"]; exists && (len(values) != 1 || values[0] != "0") {
		return "", "", false
	}
	connection, ok := one(r.Header, "Connection")
	upgrade, upgradeOK := one(r.Header, "Upgrade")
	version, versionOK := one(r.Header, "Sec-WebSocket-Version")
	key, keyOK := one(r.Header, "Sec-WebSocket-Key")
	keyBytes, err := base64.StdEncoding.DecodeString(key)
	if !ok || !strings.EqualFold(connection, "upgrade") || !upgradeOK || !strings.EqualFold(upgrade, "websocket") || !versionOK || version != "13" || !keyOK || err != nil || len(keyBytes) != 16 || base64.StdEncoding.EncodeToString(keyBytes) != key {
		return "", "", false
	}
	authorization, ok := one(r.Header, "Authorization")
	protocol, protocolOK := one(r.Header, "Sec-WebSocket-Protocol")
	if !ok || !strings.HasPrefix(authorization, "Bearer ") || !protocolOK {
		return "", "", false
	}
	size := len(r.Host) + len(r.RequestURI)
	for name, values := range r.Header {
		for _, value := range values {
			size += len(name) + len(value) + 4
		}
	}
	if size > 8192 {
		return "", "", false
	}
	return strings.TrimPrefix(authorization, "Bearer "), protocol, true
}
