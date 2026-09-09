package tunnelgate

import (
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func routingJWT(payload string) string {
	encode := base64.RawURLEncoding.EncodeToString
	return "v1, authorization.bearer." + encode([]byte(`{"typ":"JWT","alg":"HS256"}`)) + "." + encode([]byte(payload)) + "." + encode(make([]byte, 32))
}

const claims = `{"id":"550e8400-e29b-41d4-a716-446655440000","p":"ReverseTcp","r":"127.0.0.1","rp":9081}`

func TestUntrustedDescriptorCannotExpandRoute(t *testing.T) {
	if !descriptor(routingJWT(claims), "127.0.0.1:9081") {
		t.Fatal("valid untrusted route description refused")
	}
	for _, body := range []string{
		strings.Replace(claims, "ReverseTcp", "ReverseSocks5", 1),
		strings.Replace(claims, "127.0.0.1", "127.0.0.2", 1),
		strings.Replace(claims, "9081", "9082", 1),
		strings.Replace(claims, "9081", "9081.0", 1),
		strings.Replace(claims, `"rp":9081`, `"rp":9081,"rp":9082`, 1),
		strings.Replace(claims, `"rp":9081`, `"rp":9081,"unknown":true`, 1),
		strings.Replace(claims, `"r":`, `"R":`, 1),
		claims + "{}",
	} {
		if descriptor(routingJWT(body), "127.0.0.1:9081") {
			t.Fatal("routing escape accepted", body)
		}
	}
	for _, value := range []string{routingJWT(claims) + ", v2", "v1," + strings.TrimPrefix(routingJWT(claims), "v1, "), strings.Repeat("a", 2049), "", strings.Replace(routingJWT(claims), "authorization.bearer.", "", 1)} {
		if descriptor(value, "127.0.0.1:9081") {
			t.Fatal("ambiguous subprotocol accepted")
		}
	}
}

func request() *http.Request {
	r := httptest.NewRequest("GET", UpgradePath, nil)
	r.Host = "tunnel.example.test"
	r.Header = http.Header{"Connection": {"Upgrade"}, "Upgrade": {"websocket"}, "Sec-Websocket-Key": {base64.StdEncoding.EncodeToString(make([]byte, 16))}, "Sec-Websocket-Version": {"13"}, "Sec-Websocket-Protocol": {routingJWT(claims)}, "Authorization": {"Bearer synthetic"}}
	return r
}

func TestUpgradeHeadClosedProtocol(t *testing.T) {
	if _, _, ok := upgradeHead(request(), "tunnel.example.test"); !ok {
		t.Fatal("valid head denied")
	}
	for name, mutate := range map[string]func(*http.Request){
		"host":               func(r *http.Request) { r.Host = "other.example.test" },
		"method":             func(r *http.Request) { r.Method = "POST" },
		"h2":                 func(r *http.Request) { r.ProtoMajor = 2 },
		"body":               func(r *http.Request) { r.ContentLength = 1 },
		"unknown-body":       func(r *http.Request) { r.ContentLength = -1 },
		"query":              func(r *http.Request) { r.URL.RawQuery = "x=1" },
		"empty-query":        func(r *http.Request) { r.URL.ForceQuery = true },
		"cookie":             func(r *http.Request) { r.Header.Set("Cookie", "native=1") },
		"origin":             func(r *http.Request) { r.Header.Set("Origin", "https://tunnel.example.test") },
		"upgrade-list":       func(r *http.Request) { r.Header.Set("Connection", "Upgrade, keep-alive") },
		"duplicate-auth":     func(r *http.Request) { r.Header.Add("Authorization", "Bearer synthetic") },
		"duplicate-protocol": func(r *http.Request) { r.Header.Add("Sec-WebSocket-Protocol", routingJWT(claims)) },
		"extension":          func(r *http.Request) { r.Header.Set("Sec-WebSocket-Extensions", "permessage-deflate") },
		"key":                func(r *http.Request) { r.Header.Set("Sec-WebSocket-Key", "invalid") },
		"oversized":          func(r *http.Request) { r.Header.Set("X-Extra", strings.Repeat("a", 8192)) },
	} {
		t.Run(name, func(t *testing.T) {
			r := request()
			mutate(r)
			if _, _, ok := upgradeHead(r, "tunnel.example.test"); ok {
				t.Fatal("invalid head admitted")
			}
		})
	}
}
