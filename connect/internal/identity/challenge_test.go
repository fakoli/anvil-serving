package identity

import (
	"strings"
	"testing"
	"time"
)

func TestChallengeProofIsBoundAndCannotRenew(t *testing.T) {
	m, _, now, _ := identityFixture(t)
	private, installation := activeInstallation(t, m, *now)
	digest := strings.Repeat("a", 64)
	nonce, _ := randomID()
	claims, err := NewAssertion(installation.ID, m.audience+"/challenge", "connector", "router", installation.Epoch, nonce, installation.Generation, *now)
	if err != nil {
		t.Fatal(err)
	}
	claims.Fingerprint = digest
	proof, _ := Sign(private, claims)
	if _, err := m.Verify(proof, "connector", "router", nonce); err == nil {
		t.Fatal("challenge allocation proof renewed connection authority")
	}
	if m.AuthorizeChallenge(proof, installation.ID, "router", strings.Repeat("b", 64)) == nil {
		t.Fatal("challenge payload changed")
	}
	if err := m.AuthorizeChallenge(proof, installation.ID, "router", digest); err != nil {
		t.Fatal(err)
	}
	if m.AuthorizeChallenge(proof, installation.ID, "router", digest) == nil {
		t.Fatal("allocation proof replay accepted")
	}
}

func TestPermissionLifetimeEndsAtRotationBoundary(t *testing.T) {
	m, _, now, _ := identityFixture(t)
	private, installation := activeInstallation(t, m, *now)
	newPrivate, newPublic, newFingerprint := keyPair(t)
	challenge, _ := randomID()
	oldProof := rotationProof(t, m, private, installation, newFingerprint, challenge, *now)
	newProof := rotationProof(t, m, newPrivate, installation, newFingerprint, challenge, *now)
	rotated, err := m.Rotate(installation.ID, newPublic, oldProof, newProof, challenge, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	*now = rotated.PreviousUntil.Add(-3 * time.Second)
	if lifetime, err := m.PermissionLifetime(installation, "router", 45*time.Second); err != nil || lifetime != 3*time.Second {
		t.Fatal("old-key control lease extended rotation overlap", lifetime, err)
	}
	if lifetime, err := m.PermissionLifetime(rotated, "router", 45*time.Second); err != nil || lifetime != 45*time.Second {
		t.Fatal("current permission unexpectedly truncated", lifetime, err)
	}
	*now = rotated.PreviousUntil
	if _, err := m.PermissionLifetime(installation, "router", 45*time.Second); err == nil {
		t.Fatal("expired prior generation got permission")
	}
}
