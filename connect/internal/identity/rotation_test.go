package identity

import (
	"crypto/ed25519"
	"crypto/rand"
	"sync"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func activeInstallation(t *testing.T, m *Manager, now time.Time) (ed25519.PrivateKey, Installation) {
	t.Helper()
	private, installation := pending(t, m, now)
	if err := m.Approve(installation.ID, installation.Fingerprint); err != nil {
		t.Fatal(err)
	}
	installation.Status = "active"
	return private, installation
}

func keyPair(t *testing.T) (ed25519.PrivateKey, []byte, string) {
	t.Helper()
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	public, fingerprint, err := PublicKey(private)
	if err != nil {
		t.Fatal(err)
	}
	return private, public, fingerprint
}

func rotationProof(t *testing.T, m *Manager, private ed25519.PrivateKey, installation Installation, fingerprint, challenge string, now time.Time) string {
	t.Helper()
	claims, err := NewRotationAssertion(installation.ID, m.audience+"/rotate", installation.Role, installation.Epoch, challenge, fingerprint, installation.Generation, now)
	if err != nil {
		t.Fatal(err)
	}
	proof, err := Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	return proof
}

func TestRotateDualPossessionOverlapAndCheck(t *testing.T) {
	m, _, now, _ := identityFixture(t)
	oldPrivate, installation := activeInstallation(t, m, *now)
	newPrivate, newPublic, newFingerprint := keyPair(t)
	challenge, err := randomID()
	if err != nil {
		t.Fatal(err)
	}
	oldProof := rotationProof(t, m, oldPrivate, installation, newFingerprint, challenge, *now)
	newProof := rotationProof(t, m, newPrivate, installation, newFingerprint, challenge, *now)
	rotated, err := m.Rotate(installation.ID, newPublic, oldProof, newProof, challenge, DefaultRotationOverlap)
	if err != nil {
		t.Fatal(err)
	}
	if rotated.Generation != installation.Generation+1 || rotated.PreviousGeneration != installation.Generation || rotated.PreviousFingerprint != installation.Fingerprint || !rotated.PreviousUntil.Equal(now.Add(DefaultRotationOverlap)) || len(rotated.Resources) != 1 || rotated.Resources[0] != "router" {
		t.Fatalf("rotation did not retain exactly bounded previous key state: %+v", rotated)
	}
	oldProof, nonce := controlProof(t, m, oldPrivate, installation, *now)
	oldSnapshot, err := m.Verify(oldProof, "connector", "router", nonce)
	if err != nil || oldSnapshot.Generation != installation.Generation || oldSnapshot.Fingerprint != installation.Fingerprint {
		t.Fatalf("overlap did not return actual old verified key snapshot: %+v %v", oldSnapshot, err)
	}
	if err := m.Check(oldSnapshot, "router"); err != nil {
		t.Fatal(err)
	}
	newProof, nonce = controlProof(t, m, newPrivate, rotated, *now)
	newSnapshot, err := m.Verify(newProof, "connector", "router", nonce)
	if err != nil || newSnapshot.Generation != rotated.Generation || newSnapshot.Fingerprint != rotated.Fingerprint {
		t.Fatalf("new key was not admitted: %+v %v", newSnapshot, err)
	}
	if err := m.Check(newSnapshot, "router"); err != nil {
		t.Fatal(err)
	}
	for _, snapshot := range []Installation{
		{ID: newSnapshot.ID, Role: "client", PublicKey: newSnapshot.PublicKey, Fingerprint: newSnapshot.Fingerprint, Generation: newSnapshot.Generation, Epoch: newSnapshot.Epoch},
		{ID: newSnapshot.ID, Role: newSnapshot.Role, PublicKey: newSnapshot.PublicKey, Fingerprint: "different", Generation: newSnapshot.Generation, Epoch: newSnapshot.Epoch},
		{ID: newSnapshot.ID, Role: newSnapshot.Role, PublicKey: newSnapshot.PublicKey, Fingerprint: newSnapshot.Fingerprint, Generation: newSnapshot.Generation + 1, Epoch: newSnapshot.Epoch},
		{ID: newSnapshot.ID, Role: newSnapshot.Role, PublicKey: newSnapshot.PublicKey, Fingerprint: newSnapshot.Fingerprint, Generation: newSnapshot.Generation, Epoch: "0000000000000000000000000000000000000000000000000000000000000000"},
	} {
		if err := m.Check(snapshot, "router"); err == nil {
			t.Fatal("authority check accepted altered snapshot")
		}
	}
	if err := m.Check(newSnapshot, "dashboard"); err == nil {
		t.Fatal("authority check expanded the installation resource grant")
	}
	*now = rotated.PreviousUntil
	oldProof, nonce = controlProof(t, m, oldPrivate, installation, *now)
	if _, err := m.Verify(oldProof, "connector", "router", nonce); err == nil {
		t.Fatal("old key remained valid at overlap boundary")
	}
	if err := m.Check(oldSnapshot, "router"); err == nil {
		t.Fatal("old key remained valid at overlap boundary")
	}
	if err := m.Check(newSnapshot, "router"); err != nil {
		t.Fatal(err)
	}
}

func TestRotationDenialsAndSingleUse(t *testing.T) {
	for _, cause := range []string{"same-key", "swapped-key", "wrong-id", "wrong-proof-id", "old-key", "audience", "role", "generation", "epoch", "context", "challenge", "negative-overlap", "too-long-overlap", "replay", "second-overlap", "immediate"} {
		t.Run(cause, func(t *testing.T) {
			m, _, now, _ := identityFixture(t)
			oldPrivate, installation := activeInstallation(t, m, *now)
			newPrivate, newPublic, newFingerprint := keyPair(t)
			challenge, _ := randomID()
			oldProof := rotationProof(t, m, oldPrivate, installation, newFingerprint, challenge, *now)
			newProof := rotationProof(t, m, newPrivate, installation, newFingerprint, challenge, *now)
			target, overlap := installation.ID, DefaultRotationOverlap
			switch cause {
			case "same-key":
				newPublic, newFingerprint = installation.PublicKey, installation.Fingerprint
				oldProof = rotationProof(t, m, oldPrivate, installation, newFingerprint, challenge, *now)
				newProof = rotationProof(t, m, oldPrivate, installation, newFingerprint, challenge, *now)
			case "swapped-key":
				_, newPublic, _ = keyPair(t)
			case "wrong-id":
				target = "other-installation"
			case "wrong-proof-id", "old-key", "audience", "role", "generation", "epoch", "context", "challenge":
				claims, err := NewRotationAssertion(installation.ID, m.audience+"/rotate", installation.Role, installation.Epoch, challenge, newFingerprint, installation.Generation, *now)
				if err != nil {
					t.Fatal(err)
				}
				switch cause {
				case "wrong-proof-id":
					claims.Issuer, claims.Subject = "other-installation", "other-installation"
				case "old-key":
					newProof, err = Sign(oldPrivate, claims)
					if err != nil {
						t.Fatal(err)
					}
					break
				case "audience":
					claims.Audience = []string{m.audience + "/connector"}
				case "role":
					claims.Role = "client"
				case "generation":
					claims.Generation++
				case "epoch":
					claims.Epoch = "0000000000000000000000000000000000000000000000000000000000000000"
				case "context":
					claims.Resource = "router"
				case "challenge":
					claims.Nonce = "00000000000000000000000000000000"
				}
				if cause != "old-key" {
					newProof, err = Sign(newPrivate, claims)
					if err != nil {
						t.Fatal(err)
					}
				}
			case "immediate":
				overlap = 0
			case "negative-overlap":
				overlap = -time.Nanosecond
			case "too-long-overlap":
				overlap = MaximumRotationOverlap + time.Nanosecond
			}
			rotated, err := m.Rotate(target, newPublic, oldProof, newProof, challenge, overlap)
			if cause != "replay" && cause != "second-overlap" && cause != "immediate" && err == nil {
				t.Fatal("invalid rotation accepted")
			}
			if cause == "immediate" {
				if err != nil {
					t.Fatal(err)
				}
				oldControl, nonce := controlProof(t, m, oldPrivate, installation, *now)
				if _, err := m.Verify(oldControl, "connector", "router", nonce); err == nil {
					t.Fatal("immediate rotation retained old key")
				}
				return
			}
			if err != nil {
				return
			}
			if cause == "replay" {
				if _, err := m.Rotate(installation.ID, newPublic, oldProof, newProof, challenge, DefaultRotationOverlap); err == nil {
					t.Fatal("rotation replay accepted")
				}
			}
			if cause == "second-overlap" {
				thirdPrivate, thirdPublic, thirdFingerprint := keyPair(t)
				secondChallenge, _ := randomID()
				old := rotationProof(t, m, newPrivate, rotated, thirdFingerprint, secondChallenge, *now)
				next := rotationProof(t, m, thirdPrivate, rotated, thirdFingerprint, secondChallenge, *now)
				if _, err := m.Rotate(rotated.ID, thirdPublic, old, next, secondChallenge, DefaultRotationOverlap); err == nil {
					t.Fatal("second rotation admitted during overlap")
				}
			}
		})
	}
}

func TestRotationConcurrentRevokeAndReenrollment(t *testing.T) {
	m, s, now, _ := identityFixture(t)
	oldPrivate, installation := activeInstallation(t, m, *now)
	newPrivate, newPublic, newFingerprint := keyPair(t)
	challenge, _ := randomID()
	oldProof := rotationProof(t, m, oldPrivate, installation, newFingerprint, challenge, *now)
	newProof := rotationProof(t, m, newPrivate, installation, newFingerprint, challenge, *now)
	var wait sync.WaitGroup
	results := make(chan error, 2)
	for range 2 {
		wait.Add(1)
		go func() {
			defer wait.Done()
			_, err := m.Rotate(installation.ID, newPublic, oldProof, newProof, challenge, DefaultRotationOverlap)
			results <- err
		}()
	}
	wait.Wait()
	close(results)
	successes := 0
	for err := range results {
		if err == nil {
			successes++
		}
	}
	if successes != 1 {
		t.Fatalf("concurrent rotation winners=%d", successes)
	}
	if err := m.Revoke(installation.ID); err != nil {
		t.Fatal(err)
	}
	var revoked Installation
	if err := s.View(func(tx *store.Tx) error { return tx.Get("installations", installation.ID, &revoked) }); err != nil {
		t.Fatal(err)
	}
	if revoked.Status != "revoked" || revoked.Generation != installation.Generation+2 || revoked.PreviousPublicKey != nil || revoked.PreviousFingerprint != "" || revoked.PreviousGeneration != 0 || !revoked.PreviousUntil.IsZero() {
		t.Fatalf("revoke did not advance once and clear overlap state: %+v", revoked)
	}
	if err := m.Revoke(installation.ID); err != nil {
		t.Fatal(err)
	}
	var repeated Installation
	if err := s.View(func(tx *store.Tx) error { return tx.Get("installations", installation.ID, &repeated) }); err != nil {
		t.Fatal(err)
	}
	if repeated.Generation != revoked.Generation {
		t.Fatal("idempotent revoke advanced generation")
	}
	control, nonce := controlProof(t, m, newPrivate, Installation{ID: installation.ID, Role: installation.Role, Epoch: installation.Epoch, Generation: installation.Generation + 1}, *now)
	if _, err := m.Verify(control, "connector", "router", nonce); err == nil {
		t.Fatal("revoked generation authenticated")
	}
	raw, invitation, err := m.Invite(installation.ID, "connector", []string{"router"}, time.Minute)
	if err != nil || invitation.Generation != installation.Generation+3 {
		t.Fatalf("revoked installation did not recover monotonically: generation=%d err=%v", invitation.Generation, err)
	}
	_, recovered := pendingWithInvitation(t, m, raw, invitation, *now)
	if err := m.Approve(recovered.ID, recovered.Fingerprint); err != nil {
		t.Fatal(err)
	}
}

func TestRotationReplayCapacityIsExactAndDoesNotEvictLiveMarkers(t *testing.T) {
	m, s, now, _ := identityFixture(t)
	oldPrivate, installation := activeInstallation(t, m, *now)
	newPrivate, newPublic, newFingerprint := keyPair(t)
	challenge, _ := randomID()
	oldProof := rotationProof(t, m, oldPrivate, installation, newFingerprint, challenge, *now)
	newProof := rotationProof(t, m, newPrivate, installation, newFingerprint, challenge, *now)
	window := replayWindow{Entries: map[string]time.Time{}}
	for range 510 {
		marker, err := randomID()
		if err != nil {
			t.Fatal(err)
		}
		window.Entries[marker] = now.Add(MaxProofLifetime)
	}
	if err := s.Update(func(tx *store.Tx) error { return tx.Put("replay", installation.ID, window) }); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Rotate(installation.ID, newPublic, oldProof, newProof, challenge, DefaultRotationOverlap); err == nil {
		t.Fatal("rotation evicted live replay markers")
	}
	for marker := range window.Entries {
		delete(window.Entries, marker)
		break
	}
	if len(window.Entries) != 509 {
		t.Fatal("test did not remove one marker")
	}
	// A rotation needs exactly three fresh replay markers, so 509 is the
	// inclusive accepted boundary.
	if err := s.Update(func(tx *store.Tx) error { return tx.Put("replay", installation.ID, window) }); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Rotate(installation.ID, newPublic, oldProof, newProof, challenge, DefaultRotationOverlap); err != nil {
		t.Fatal("rotation rejected exact replay capacity", err)
	}
}

func TestRotationAuthorityResetDoesNotReactivateEitherKey(t *testing.T) {
	m, s, now, _ := identityFixture(t)
	oldPrivate, installation := activeInstallation(t, m, *now)
	newPrivate, newPublic, newFingerprint := keyPair(t)
	challenge, _ := randomID()
	oldProof := rotationProof(t, m, oldPrivate, installation, newFingerprint, challenge, *now)
	newProof := rotationProof(t, m, newPrivate, installation, newFingerprint, challenge, *now)
	rotated, err := m.Rotate(installation.ID, newPublic, oldProof, newProof, challenge, DefaultRotationOverlap)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	for _, candidate := range []struct {
		private      ed25519.PrivateKey
		installation Installation
	}{
		{oldPrivate, installation},
		{newPrivate, rotated},
	} {
		proof, nonce := controlProof(t, m, candidate.private, candidate.installation, *now)
		if _, err := m.Verify(proof, "connector", "router", nonce); err == nil {
			t.Fatal("authority reset reactivated an installation key")
		}
	}
	if err := m.Check(rotated, "router"); err == nil {
		t.Fatal("authority reset accepted an old local snapshot")
	}
	raw, invitation, err := m.Invite(installation.ID, "connector", []string{"router"}, time.Minute)
	if err != nil || invitation.Generation != rotated.Generation+1 {
		t.Fatal("authority reset did not permit monotonic owner recovery", err)
	}
	_, recovered := pendingWithInvitation(t, m, raw, invitation, *now)
	if err := m.Approve(recovered.ID, recovered.Fingerprint); err != nil {
		t.Fatal(err)
	}
}

func pendingWithInvitation(t *testing.T, m *Manager, raw string, invitation Invitation, now time.Time) (ed25519.PrivateKey, Installation) {
	t.Helper()
	private, public, _ := keyPair(t)
	claims, err := NewAssertion(invitation.Installation, m.audience+"/enroll", invitation.Role, "", invitation.Epoch, EnrollmentNonce(raw), 0, now)
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
