package identity

import (
	"crypto/ed25519"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/store"
	jose "github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

func controlClaims(t *testing.T, m *Manager, installation Installation, now time.Time) Assertion {
	t.Helper()
	nonce, err := randomID()
	if err != nil {
		t.Fatal(err)
	}
	claims, err := NewAssertion(installation.ID, m.audience+"/connector", "connector", "router", installation.Epoch, nonce, installation.Generation, now)
	if err != nil {
		t.Fatal(err)
	}
	return claims
}

func controlProof(t *testing.T, m *Manager, private ed25519.PrivateKey, installation Installation, now time.Time) (string, string) {
	t.Helper()
	claims := controlClaims(t, m, installation, now)
	proof, err := Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	return proof, claims.Nonce
}

func TestAssertionDenials(t *testing.T) {
	for _, cause := range []string{"issuer", "subject", "audience", "multi-audience", "nonce", "jti", "expiry", "no-expiry", "future", "lifetime", "generation", "epoch", "resource", "role", "algorithm", "key-url", "wrong-type", "revoked"} {
		t.Run(cause, func(t *testing.T) {
			m, s, now, _ := identityFixture(t)
			private, installation := pending(t, m, *now)
			if err := m.Approve(installation.ID, installation.Fingerprint); err != nil {
				t.Fatal(err)
			}
			claims := controlClaims(t, m, installation, *now)
			expectedNonce := claims.Nonce
			algorithm, key, typ := jose.EdDSA, any(private), jose.ContentType(proofType)
			switch cause {
			case "issuer":
				claims.Issuer = "other-installation"
			case "subject":
				claims.Subject = "other-installation"
			case "audience":
				claims.Audience = jwt.Audience{"https://other.example.test/connector"}
			case "multi-audience":
				claims.Audience = append(claims.Audience, "https://other.example.test/connector")
			case "nonce":
				expectedNonce = "00000000000000000000000000000000"
			case "jti":
				claims.ID = ""
			case "expiry":
				*now = claims.Expiry.Time()
			case "no-expiry":
				claims.Expiry = nil
			case "future":
				claims.IssuedAt = jwt.NewNumericDate(now.Add(time.Second))
			case "lifetime":
				claims.Expiry = jwt.NewNumericDate(now.Add(time.Hour))
			case "generation":
				claims.Generation++
			case "epoch":
				claims.Epoch = strings.Repeat("0", 64)
			case "resource":
				claims.Resource = "dashboard"
			case "role":
				claims.Role = "client"
			case "algorithm":
				algorithm = jose.HS256
				key = []byte(private.Public().(ed25519.PublicKey))
			case "wrong-type":
				typ = "JWT"
			case "revoked":
				if err := s.Update(func(tx *store.Tx) error {
					installation.Status = "revoked"
					return tx.Put("installations", installation.ID, installation)
				}); err != nil {
					t.Fatal(err)
				}
			}
			options := (&jose.SignerOptions{}).WithType(typ).WithHeader("kid", installation.ID)
			if cause == "key-url" {
				options.WithHeader("jku", "https://untrusted.example.test/keys")
			}
			signer, err := jose.NewSigner(jose.SigningKey{Algorithm: algorithm, Key: key}, options)
			if err != nil {
				t.Fatal(err)
			}
			proof, err := jwt.Signed(signer).Claims(claims).Serialize()
			if err != nil {
				t.Fatal(err)
			}
			if _, err := m.Verify(proof, "connector", "router", expectedNonce); err == nil {
				t.Fatal("invalid installation assertion accepted")
			}
		})
	}
}

func TestNonceAndJWTReplaySurviveRestart(t *testing.T) {
	m, s, now, directory := identityFixture(t)
	private, installation := pending(t, m, *now)
	if err := m.Approve(installation.ID, installation.Fingerprint); err != nil {
		t.Fatal(err)
	}
	claims := controlClaims(t, m, installation, *now)
	proof, err := Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := m.Verify(proof, "connector", "router", claims.Nonce); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := store.Open(directory, func() time.Time { return *now })
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	fresh, err := NewManager(reopened, m.audience, []string{"router", "dashboard"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fresh.Verify(proof, "connector", "router", claims.Nonce); err == nil {
		t.Fatal("restart forgot replay marker")
	}
	for _, change := range []string{"jti", "nonce"} {
		modified := claims
		replacement, err := randomID()
		if err != nil {
			t.Fatal(err)
		}
		if change == "jti" {
			modified.ID = replacement
		} else {
			modified.Nonce = replacement
		}
		encoded, err := Sign(private, modified)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := fresh.Verify(encoded, "connector", "router", modified.Nonce); err == nil {
			t.Fatal("reused nonce or JWT ID accepted")
		}
	}
	if err := reopened.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	proof, nonce := controlProof(t, fresh, private, installation, *now)
	if _, err := fresh.Verify(proof, "connector", "router", nonce); err == nil {
		t.Fatal("restore epoch reactivated old installation")
	}
}

func TestReplayWindowRefusesOverflow(t *testing.T) {
	m, s, now, _ := identityFixture(t)
	private, installation := pending(t, m, *now)
	if err := m.Approve(installation.ID, installation.Fingerprint); err != nil {
		t.Fatal(err)
	}
	window := replayWindow{Entries: map[string]time.Time{}}
	for i := 0; i < 512; i++ {
		id, err := randomID()
		if err != nil {
			t.Fatal(err)
		}
		window.Entries[id] = now.Add(MaxProofLifetime)
	}
	if err := s.Update(func(tx *store.Tx) error { return tx.Put("replay", installation.ID, window) }); err != nil {
		t.Fatal(err)
	}
	proof, nonce := controlProof(t, m, private, installation, *now)
	if _, err := m.Verify(proof, "connector", "router", nonce); err == nil {
		t.Fatal("unexpired replay marker evicted to admit overflow")
	}
	*now = now.Add(MaxProofLifetime)
	proof, nonce = controlProof(t, m, private, installation, *now)
	if _, err := m.Verify(proof, "connector", "router", nonce); err != nil {
		t.Fatal("expired markers not reclaimed", err)
	}
}
