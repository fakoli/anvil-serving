package identity

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/store"
	jose "github.com/go-jose/go-jose/v4"
)

func identityFixture(t *testing.T) (*Manager, *store.Store, *time.Time, string) {
	t.Helper()
	now := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	directory := filepath.Join(t.TempDir(), "authority")
	s, err := store.Open(directory, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	m, err := NewManager(s, "https://connect.example.test", []string{"router", "dashboard"})
	if err != nil {
		t.Fatal(err)
	}
	return m, s, &now, directory
}

func pending(t *testing.T, m *Manager, now time.Time) (ed25519.PrivateKey, Installation) {
	t.Helper()
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	public, _, err := PublicKey(private)
	if err != nil {
		t.Fatal(err)
	}
	raw, invitation, err := m.Invite("origin-a", "connector", []string{"router"}, 5*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	claims, err := NewAssertion("origin-a", m.audience+"/enroll", "connector", "", invitation.Epoch, EnrollmentNonce(raw), 0, now)
	if err != nil {
		t.Fatal(err)
	}
	proof, err := Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	installation, err := m.Enroll(raw, public, proof)
	if err != nil {
		t.Fatal(err)
	}
	return private, installation
}

func TestEnrollmentRequiresOwnerFingerprint(t *testing.T) {
	m, _, now, _ := identityFixture(t)
	private, installation := pending(t, m, *now)
	if installation.Status != "pending" || installation.Role != "connector" || len(installation.Resources) != 1 || installation.Resources[0] != "router" {
		t.Fatal("enrollment changed granted role or resources")
	}
	proof, nonce := controlProof(t, m, private, installation, *now)
	if _, err := m.Verify(proof, "connector", "router", nonce); err == nil {
		t.Fatal("pending installation authorized")
	}
	if err := m.Approve(installation.ID, "wrong fingerprint"); err == nil {
		t.Fatal("wrong fingerprint approved")
	}
	if err := m.Approve(installation.ID, installation.Fingerprint); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Verify(proof, "connector", "router", nonce); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Verify(proof, "client", "router", nonce); err == nil {
		t.Fatal("connector credential became user/client identity")
	}
}

func TestInvitationAtomicConsumption(t *testing.T) {
	m, s, now, directory := identityFixture(t)
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	public, _, err := PublicKey(private)
	if err != nil {
		t.Fatal(err)
	}
	raw, inv, err := m.Invite("origin-a", "connector", []string{"router"}, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	claims, err := NewAssertion(inv.Installation, m.audience+"/enroll", inv.Role, "", inv.Epoch, EnrollmentNonce(raw), 0, *now)
	if err != nil {
		t.Fatal(err)
	}
	proof, err := Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	var successes atomic.Int32
	var wait sync.WaitGroup
	for i := 0; i < 12; i++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			if _, err := m.Enroll(raw, public, proof); err == nil {
				successes.Add(1)
			}
		}()
	}
	wait.Wait()
	if successes.Load() != 1 {
		t.Fatal("invitation did not have exactly one winner")
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(directory, "state.db"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(data, []byte(raw)) || bytes.Contains(data, private.Seed()) {
		t.Fatal("invitation or private key persisted")
	}
}

func TestEnrollmentDenials(t *testing.T) {
	for _, cause := range []string{"expiry", "wrong-proof-key", "wrong-invitation", "wrong-nonce", "private-jwk", "unknown-role", "approval-expiry"} {
		t.Run(cause, func(t *testing.T) {
			m, _, now, _ := identityFixture(t)
			_, private, err := ed25519.GenerateKey(rand.Reader)
			if err != nil {
				t.Fatal(err)
			}
			public, _, err := PublicKey(private)
			if err != nil {
				t.Fatal(err)
			}
			raw, inv, err := m.Invite("origin-a", "connector", []string{"router"}, time.Minute)
			if err != nil {
				t.Fatal(err)
			}
			claims, err := NewAssertion(inv.Installation, m.audience+"/enroll", inv.Role, "", inv.Epoch, EnrollmentNonce(raw), 0, *now)
			if err != nil {
				t.Fatal(err)
			}
			switch cause {
			case "expiry":
				*now = inv.ExpiresAt
			case "wrong-proof-key":
				_, private, err = ed25519.GenerateKey(rand.Reader)
				if err != nil {
					t.Fatal(err)
				}
			case "wrong-invitation":
				raw = raw[:len(raw)-1] + "!"
			case "wrong-nonce":
				claims.Nonce = "00000000000000000000000000000000"
			case "private-jwk":
				public, err = json.Marshal(jose.JSONWebKey{Key: private})
				if err != nil {
					t.Fatal(err)
				}
			case "unknown-role":
				claims.Role = "administrator"
			}
			proof, err := Sign(private, claims)
			if err != nil {
				t.Fatal(err)
			}
			installation, err := m.Enroll(raw, public, proof)
			if cause == "approval-expiry" {
				if err != nil {
					t.Fatal(err)
				}
				*now = installation.ApprovalExpiresAt
				if err := m.Approve(installation.ID, installation.Fingerprint); err == nil {
					t.Fatal("expired pending approval accepted")
				}
			} else if err == nil {
				t.Fatal("invalid enrollment accepted")
			}
		})
	}
}

func TestInviteBounds(t *testing.T) {
	m, _, _, _ := identityFixture(t)
	for _, role := range []string{"admin", "gateway", "user", ""} {
		if _, _, err := m.Invite("origin-a", role, []string{"router"}, time.Minute); err == nil {
			t.Fatal("undeclared role issued")
		}
	}
	for _, resources := range [][]string{nil, {"unknown"}, {"router", "router"}} {
		if _, _, err := m.Invite("origin-a", "connector", resources, time.Minute); err == nil {
			t.Fatal("invalid resource grants issued")
		}
	}
	for _, lifetime := range []time.Duration{0, time.Second, 11 * time.Minute} {
		if _, _, err := m.Invite("origin-a", "connector", []string{"router"}, lifetime); err == nil {
			t.Fatal("unbounded invitation issued")
		}
	}
}

func TestAdministrativeReenrollment(t *testing.T) {
	for _, cause := range []string{"expired-pending", "expired-invitation", "epoch-reset", "active"} {
		t.Run(cause, func(t *testing.T) {
			m, s, now, _ := identityFixture(t)
			var generation uint64
			if cause == "expired-invitation" {
				_, inv, err := m.Invite("origin-a", "connector", []string{"router"}, time.Minute)
				if err != nil {
					t.Fatal(err)
				}
				generation = inv.Generation
				*now = inv.ExpiresAt
			} else {
				_, installation := pending(t, m, *now)
				generation = installation.Generation
				switch cause {
				case "expired-pending":
					*now = installation.ApprovalExpiresAt
				case "epoch-reset":
					if err := s.ResetAuthority(); err != nil {
						t.Fatal(err)
					}
				case "active":
					if err := m.Approve(installation.ID, installation.Fingerprint); err != nil {
						t.Fatal(err)
					}
				}
			}
			raw, inv, err := m.Invite("origin-a", "connector", []string{"router"}, time.Minute)
			if cause == "active" {
				if err == nil {
					t.Fatal("active installation replaced")
				}
				return
			}
			if err != nil || inv.Generation != generation+1 {
				t.Fatal("administrative recovery did not advance generation", err)
			}
			_, private, err := ed25519.GenerateKey(rand.Reader)
			if err != nil {
				t.Fatal(err)
			}
			public, _, err := PublicKey(private)
			if err != nil {
				t.Fatal(err)
			}
			claims, err := NewAssertion(inv.Installation, m.audience+"/enroll", inv.Role, "", inv.Epoch, EnrollmentNonce(raw), 0, *now)
			if err != nil {
				t.Fatal(err)
			}
			proof, err := Sign(private, claims)
			if err != nil {
				t.Fatal(err)
			}
			installation, err := m.Enroll(raw, public, proof)
			if err != nil {
				t.Fatal(err)
			}
			if err := m.Approve(installation.ID, installation.Fingerprint); err != nil {
				t.Fatal(err)
			}
			control, nonce := controlProof(t, m, private, installation, *now)
			if _, err := m.Verify(control, "connector", "router", nonce); err != nil {
				t.Fatal("replacement could not authenticate", err)
			}
		})
	}
}
