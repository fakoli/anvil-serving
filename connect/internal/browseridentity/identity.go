// Package browseridentity mints the short-lived gateway assertion that a
// browser-origin application verifies independently of caller-controlled
// headers. It deliberately does not model installation control assertions.
package browseridentity

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

const (
	Header       = "X-Anvil-Connect-Identity"
	prefix       = "acai1"
	maxTokenSize = 2048
	lifetime     = 30 * time.Second
)

var ErrDenied = errors.New("browser identity assertion denied")

type Signer struct {
	key    [32]byte
	kid    string
	now    func() time.Time
	random io.Reader
}

// NewSigner accepts the exact deployment secret grammar: 32 bytes encoded as
// canonical raw base64url. The secret is never returned or placed in an error.
func NewSigner(secret, kid string) (*Signer, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(secret)
	if err != nil || len(decoded) != len(([32]byte{})) || base64.RawURLEncoding.EncodeToString(decoded) != secret || !config.ValidID(kid) {
		return nil, ErrDenied
	}
	result := &Signer{kid: kid, now: time.Now, random: rand.Reader}
	copy(result.key[:], decoded)
	return result, nil
}

type claims struct {
	V            int    `json:"v"`
	Issuer       string `json:"iss"`
	KeyID        string `json:"kid"`
	Subject      string `json:"sub"`
	SessionID    string `json:"sid"`
	SessionGen   uint64 `json:"sg"`
	PrincipalGen uint64 `json:"pg"`
	Epoch        string `json:"epoch"`
	Resource     string `json:"resource"`
	Role         string `json:"role,omitempty"`
	Host         string `json:"host"`
	Method       string `json:"method"`
	TargetSHA256 string `json:"target_sha256"`
	IssuedAt     int64  `json:"iat"`
	ExpiresAt    int64  `json:"exp"`
	SessionExp   int64  `json:"session_exp"`
	JTI          string `json:"jti"`
}

func lowerHex(value string, bytes int) bool {
	if len(value) != bytes*2 {
		return false
	}
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == bytes && hex.EncodeToString(decoded) == value
}

func validOpaquePrincipal(value string) bool {
	// Connect currently produces a hashed issuer/subject projection. Keep this
	// value opaque to the assertion contract while refusing control characters,
	// unbounded text, and non-canonical Unicode representations.
	return len(value) >= 1 && len(value) <= 192 && utf8.ValidString(value) && !strings.ContainsAny(value, "\x00\r\n\t")
}

func validAdmission(admitted session.Admission, rule config.Rule, request *http.Request, now time.Time) bool {
	return request != nil && request.URL != nil && validOpaquePrincipal(admitted.Principal) && lowerHex(admitted.SessionID, 16) && admitted.SessionGeneration > 0 && admitted.PrincipalGeneration > 0 && lowerHex(admitted.Epoch, 32) && admitted.Resource == rule.ID && admitted.Host == rule.Host && !admitted.ExpiresAt.IsZero() && (admitted.ApplicationRole == "" || admitted.ApplicationRole == "member" || admitted.ApplicationRole == "admin") && config.ValidID(rule.ID) && config.ValidHost(rule.Host) && config.ValidMethod(request.Method) && rule.Allows(request.Host, request.URL.Path, request.Method) && admitted.ExpiresAt.After(now)
}

// Sign creates a request-bound assertion after browser admission has been
// freshly checked. The exact JSON field order is the cross-language wire
// format; do not replace it with a map.
func (s *Signer) Sign(admitted session.Admission, rule config.Rule, request *http.Request) (string, error) {
	if s == nil || s.now == nil || s.random == nil || len(s.key) != 32 || !config.ValidID(s.kid) {
		return "", ErrDenied
	}
	now := s.now().UTC().Truncate(time.Second)
	if !validAdmission(admitted, rule, request, now) {
		return "", ErrDenied
	}
	sessionExp := admitted.ExpiresAt.UTC().Unix()
	expires := now.Add(lifetime).Unix()
	if sessionExp < expires {
		expires = sessionExp
	}
	if expires <= now.Unix() {
		return "", ErrDenied
	}
	jtiRaw := make([]byte, 16)
	if _, err := io.ReadFull(s.random, jtiRaw); err != nil {
		return "", ErrDenied
	}
	target := sha256.Sum256([]byte(request.URL.RequestURI()))
	payload, err := json.Marshal(claims{
		V: 1, Issuer: "anvil-connect", KeyID: s.kid, Subject: admitted.Principal,
		SessionID: admitted.SessionID, SessionGen: admitted.SessionGeneration, PrincipalGen: admitted.PrincipalGeneration,
		Epoch: admitted.Epoch, Resource: rule.ID, Role: admitted.ApplicationRole, Host: rule.Host, Method: request.Method,
		TargetSHA256: hex.EncodeToString(target[:]), IssuedAt: now.Unix(), ExpiresAt: expires,
		SessionExp: sessionExp, JTI: hex.EncodeToString(jtiRaw),
	})
	if err != nil || len(payload) > 1024 {
		return "", ErrDenied
	}
	encoded := base64.RawURLEncoding.EncodeToString(payload)
	mac := hmac.New(sha256.New, s.key[:])
	_, _ = mac.Write([]byte(prefix + "." + encoded))
	return prefix + "." + encoded + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil)), nil
}

// ValidWire checks only the bounded transport envelope. The connector has no
// signing key and must leave claim and MAC verification to the local native
// application; it may relay only an assertion that arrived over gateway mTLS.
func ValidWire(value string) bool {
	if len(value) < len(prefix)+1+2 || len(value) > maxTokenSize || strings.Count(value, ".") != 2 {
		return false
	}
	parts := strings.Split(value, ".")
	if len(parts) != 3 || parts[0] != prefix || parts[1] == "" || parts[2] == "" {
		return false
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil || len(payload) > 1024 || base64.RawURLEncoding.EncodeToString(payload) != parts[1] || !json.Valid(payload) {
		return false
	}
	mac, err := base64.RawURLEncoding.DecodeString(parts[2])
	return err == nil && len(mac) == sha256.Size && base64.RawURLEncoding.EncodeToString(mac) == parts[2]
}

type contextKey struct{}

func WithAssertion(ctx context.Context, assertion string) context.Context {
	return context.WithValue(ctx, contextKey{}, assertion)
}

func Assertion(ctx context.Context) (string, bool) {
	value, ok := ctx.Value(contextKey{}).(string)
	return value, ok && ValidWire(value)
}
