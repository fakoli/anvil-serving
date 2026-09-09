package identity

import (
	"crypto/ed25519"
	"encoding/hex"
	"encoding/json"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	jose "github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

const MaxProofLifetime = 30 * time.Second
const proofType = "anvil-connect-installation+jwt"

type Assertion struct {
	jwt.Claims
	Role        string `json:"role"`
	Resource    string `json:"resource"`
	Epoch       string `json:"epoch"`
	Generation  uint64 `json:"generation"`
	Nonce       string `json:"nonce"`
	Fingerprint string `json:"fingerprint,omitempty"`
}

// NewRotationAssertion binds both possession proofs to the same server-issued
// challenge and exact proposed public-key fingerprint.
func NewRotationAssertion(id, audience, role, epoch, challenge, fingerprint string, generation uint64, now time.Time) (Assertion, error) {
	claims, err := NewAssertion(id, audience, role, "", epoch, challenge, generation, now)
	if err != nil || fingerprint == "" || len(fingerprint) > 256 {
		return Assertion{}, ErrDenied
	}
	claims.Fingerprint = fingerprint
	return claims, nil
}

func validNonce(value string) bool {
	if len(value) != 32 && len(value) != 64 {
		return false
	}
	decoded, err := hex.DecodeString(value)
	return err == nil && hex.EncodeToString(decoded) == value
}

// NewAssertion creates a standard JWT claim set; signing remains a separate
// operation on the installation's locally generated private key.
func NewAssertion(id, audience, role, resource, epoch, nonce string, generation uint64, now time.Time) (Assertion, error) {
	if !config.ValidID(id) || !validRole(role) || !validNonce(nonce) || len(epoch) != 64 {
		return Assertion{}, ErrDenied
	}
	jti, err := randomID()
	if err != nil {
		return Assertion{}, err
	}
	now = now.UTC().Truncate(time.Second)
	return Assertion{Claims: jwt.Claims{Issuer: id, Subject: id, Audience: jwt.Audience{audience}, ID: jti, IssuedAt: jwt.NewNumericDate(now), NotBefore: jwt.NewNumericDate(now), Expiry: jwt.NewNumericDate(now.Add(MaxProofLifetime))}, Role: role, Resource: resource, Epoch: epoch, Generation: generation, Nonce: nonce}, nil
}

func Sign(private ed25519.PrivateKey, claims Assertion) (string, error) {
	if len(private) != ed25519.PrivateKeySize || !config.ValidID(claims.Issuer) {
		return "", ErrDenied
	}
	signer, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.EdDSA, Key: private}, (&jose.SignerOptions{}).WithType(proofType).WithHeader("kid", claims.Issuer))
	if err != nil {
		return "", ErrDenied
	}
	encoded, err := jwt.Signed(signer).Claims(claims).Serialize()
	if err != nil {
		return "", ErrDenied
	}
	return encoded, nil
}

func parse(proof string) (*jwt.JSONWebToken, error) {
	if len(proof) > 16384 || strings.Count(proof, ".") != 2 {
		return nil, ErrDenied
	}
	token, err := jwt.ParseSigned(proof, []jose.SignatureAlgorithm{jose.EdDSA})
	if err != nil || len(token.Headers) != 1 {
		return nil, ErrDenied
	}
	h := token.Headers[0]
	if !config.ValidID(h.KeyID) || h.Algorithm != string(jose.EdDSA) || h.JSONWebKey != nil || h.Nonce != "" || len(h.ExtraHeaders) != 1 || h.ExtraHeaders[jose.HeaderType] != proofType {
		return nil, ErrDenied
	}
	return token, nil
}

func verify(proof string, key ed25519.PublicKey, id, audience string, now time.Time) (Assertion, error) {
	token, err := parse(proof)
	if err != nil || token.Headers[0].KeyID != id {
		return Assertion{}, ErrDenied
	}
	var claims Assertion
	if token.Claims(key, &claims) != nil || claims.IssuedAt == nil || claims.NotBefore == nil || claims.Expiry == nil || !validNonce(claims.ID) || len(claims.Audience) != 1 || !validNonce(claims.Nonce) {
		return Assertion{}, ErrDenied
	}
	if claims.ValidateWithLeeway(jwt.Expected{Issuer: id, Subject: id, AnyAudience: jwt.Audience{audience}, Time: now}, 0) != nil || !now.Before(claims.Expiry.Time()) || !claims.Expiry.Time().After(claims.IssuedAt.Time()) || claims.Expiry.Time().Sub(claims.IssuedAt.Time()) > MaxProofLifetime {
		return Assertion{}, ErrDenied
	}
	return claims, nil
}

type replayWindow struct {
	Entries map[string]time.Time `json:"entries"`
}

// Verify is for installation control connections, not individual application
// requests. expectedNonce must come from the server's connection challenge,
// never from a caller-selected header. Both nonce and JWT ID are consumed once.
// The bounded per-installation window refuses overflow rather than evicting an
// unexpired replay marker. Data-plane authorization uses separate API/sessions.
func (m *Manager) Verify(proof, expectedRole, resource, expectedNonce string) (Installation, error) {
	if !validRole(expectedRole) || !m.resources[resource] || !validNonce(expectedNonce) {
		return Installation{}, ErrDenied
	}
	return m.verifyControl(proof, expectedRole, resource, expectedNonce, m.audience+"/"+expectedRole, "")
}

// AuthorizeChallenge admits only a challenge-allocation request. It consumes
// a short-lived, replay-protected possession proof under a separate audience;
// its caller-generated nonce cannot authorize renewal or tunnel access. The
// payload digest and resource bind the requested challenge before allocation.
// No installation snapshot is returned as data-plane authority.
func (m *Manager) AuthorizeChallenge(proof, id, resource, digest string) error {
	if !config.ValidID(id) || !m.resources[resource] || len(digest) != 64 || !validNonce(digest) {
		return ErrDenied
	}
	snapshot, err := m.verifyControl(proof, "connector", resource, "", m.audience+"/challenge", digest)
	if err != nil || snapshot.ID != id {
		return ErrDenied
	}
	return nil
}

func (m *Manager) verifyControl(proof, expectedRole, resource, expectedNonce, audience, fingerprint string) (Installation, error) {
	token, err := parse(proof)
	if err != nil {
		return Installation{}, ErrDenied
	}
	id := token.Headers[0].KeyID
	var result Installation
	err = m.state.Update(func(tx *store.Tx) error {
		var installation Installation
		if tx.Get("installations", id, &installation) != nil || !activeForResource(installation, tx, resource) || installation.Role != expectedRole {
			return ErrDenied
		}
		key, _, err := parsePublic(installation.PublicKey)
		if err != nil {
			return ErrDenied
		}
		claims, err := verify(proof, key, id, audience, tx.Now())
		result = installation
		matches := func(claims Assertion) bool {
			return claims.Role == expectedRole && claims.Resource == resource && (expectedNonce == "" || claims.Nonce == expectedNonce) && claims.Epoch == tx.Epoch() && (fingerprint == "" || claims.Fingerprint == fingerprint)
		}
		if err != nil || !matches(claims) {
			if installation.PreviousUntil.IsZero() || !tx.Now().Before(installation.PreviousUntil) {
				return ErrDenied
			}
			key, _, err = parsePublic(installation.PreviousPublicKey)
			if err != nil {
				return ErrDenied
			}
			claims, err = verify(proof, key, id, audience, tx.Now())
			if err != nil || !matches(claims) || claims.Generation != installation.PreviousGeneration {
				return ErrDenied
			}
			result.PublicKey = append(json.RawMessage(nil), installation.PreviousPublicKey...)
			result.Fingerprint = installation.PreviousFingerprint
			result.Generation = installation.PreviousGeneration
		} else if claims.Generation != installation.Generation {
			return ErrDenied
		}
		if err := m.consumeReplay(tx, id, map[string]time.Time{"jti:" + claims.ID: claims.Expiry.Time(), "nonce:" + claims.Nonce: claims.Expiry.Time()}); err != nil {
			return err
		}
		return nil
	})
	if err != nil {
		return Installation{}, ErrDenied
	}
	return result, nil
}
