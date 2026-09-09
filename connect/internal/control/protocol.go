// Package control implements the bounded installation enrollment and renewal
// protocol. Browser cookies and API keys never authorize this control plane.
package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"strings"
)

const Schema = "anvil-connect.control/v1"
const maxBody = 65536

var ErrDenied = errors.New("installation control denied")

type ChallengeRequest struct {
	Installation string `json:"installation"`
	Resource     string `json:"resource"`
	Purpose      string `json:"purpose"`
	Digest       string `json:"digest"`
	Proof        string `json:"proof"`
}

type ChallengeResponse struct {
	Schema string `json:"schema"`
	Nonce  string `json:"nonce"`
}

type EnrollmentRequest struct {
	Invitation string `json:"invitation"`
	PublicKey  string `json:"public_key"`
	Proof      string `json:"proof"`
}

type InstallationResponse struct {
	Schema       string `json:"schema"`
	Installation string `json:"installation"`
	Role         string `json:"role"`
	Status       string `json:"status"`
	Epoch        string `json:"epoch"`
	Generation   uint64 `json:"generation"`
	Fingerprint  string `json:"fingerprint"`
}

type RenewalRequest struct {
	Installation string `json:"installation"`
	Resource     string `json:"resource"`
	Nonce        string `json:"nonce"`
	Proof        string `json:"proof"`
	CSR          string `json:"csr"`
}

type RenewalResponse struct {
	Schema            string `json:"schema"`
	Installation      string `json:"installation"`
	Resource          string `json:"resource"`
	Epoch             string `json:"epoch"`
	Generation        uint64 `json:"generation"`
	LeaseMilliseconds int64  `json:"lease_milliseconds"`
	TransportToken    string `json:"transport_token"`
	Certificate       string `json:"certificate"`
}

type RotationRequest struct {
	Installation        string `json:"installation"`
	Resource            string `json:"resource"`
	Nonce               string `json:"nonce"`
	PublicKey           string `json:"public_key"`
	OldProof            string `json:"old_proof"`
	NewProof            string `json:"new_proof"`
	OverlapMilliseconds int64  `json:"overlap_milliseconds"`
}

func RenewalDigest(csr []byte) string {
	value := sha256.Sum256(append([]byte("anvil-connect/renew/v1\x00"), csr...))
	return hex.EncodeToString(value[:])
}

func RotationDigest(public []byte, overlapMilliseconds int64) string {
	data, _ := json.Marshal(struct {
		Purpose string
		Public  []byte
		Overlap int64
	}{"anvil-connect/rotate/v1", public, overlapMilliseconds})
	value := sha256.Sum256(data)
	return hex.EncodeToString(value[:])
}

func validDigest(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 32 && hex.EncodeToString(decoded) == value
}

func encoded(data []byte) string { return base64.RawURLEncoding.EncodeToString(data) }
func decoded(value string, maximum int) ([]byte, error) {
	if len(value) > 2*maximum {
		return nil, ErrDenied
	}
	data, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil || len(data) > maximum || encoded(data) != value {
		return nil, ErrDenied
	}
	return data, nil
}

// Wire structs contain only explicitly tagged scalar values. Closed parsing
// rejects case aliases, duplicate/unknown fields, nulls and trailing input.
func decode(reader io.Reader, target any) error {
	data, err := io.ReadAll(io.LimitReader(reader, maxBody+1))
	if err != nil || len(data) > maxBody {
		return ErrDenied
	}
	t := reflect.TypeOf(target)
	if t == nil || t.Kind() != reflect.Pointer || t.Elem().Kind() != reflect.Struct {
		return ErrDenied
	}
	t = t.Elem()
	allowed := map[string]bool{}
	for n := 0; n < t.NumField(); n++ {
		allowed[strings.Split(t.Field(n).Tag.Get("json"), ",")[0]] = true
	}
	d := json.NewDecoder(bytes.NewReader(data))
	start, err := d.Token()
	if err != nil || start != json.Delim('{') {
		return ErrDenied
	}
	seen := map[string]bool{}
	for d.More() {
		token, err := d.Token()
		key, ok := token.(string)
		if err != nil || !ok || !allowed[key] || seen[key] {
			return ErrDenied
		}
		seen[key] = true
		var raw json.RawMessage
		if d.Decode(&raw) != nil || bytes.Equal(raw, []byte("null")) {
			return ErrDenied
		}
	}
	if end, err := d.Token(); err != nil || end != json.Delim('}') {
		return ErrDenied
	}
	if _, err := d.Token(); err != io.EOF {
		return ErrDenied
	}
	if json.Unmarshal(data, target) != nil {
		return ErrDenied
	}
	return nil
}
