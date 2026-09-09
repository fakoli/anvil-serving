package credential

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"fmt"
	"math/big"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

type credentialFixture struct {
	issuer       *Issuer
	identity     *identity.Manager
	state        *store.Store
	now          *time.Time
	installation identity.Installation
	josePrivate  ed25519.PrivateKey
	ca           *x509.Certificate
	caPrivate    ed25519.PrivateKey
}

func credentialGateway() config.Gateway {
	limits := config.Limits{RequestBytes: 1024, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 60}
	return config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:17890", MaxConcurrent: 1, Resources: []config.Resource{{Rule: config.Rule{ID: "dash", Host: "dash.example.test", PathPrefix: "/", Methods: []string{"GET"}, Access: "browser", NativeAuth: "none", Limits: limits}, Connector: "connector-a", TunnelAddress: "127.0.0.1:17891"}}}
}

func credentialCA(t *testing.T, now time.Time) (*x509.Certificate, ed25519.PrivateKey) {
	t.Helper()
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "connect test ca"}, NotBefore: now.Add(-time.Minute), NotAfter: now.Add(24 * time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der, err := x509.CreateCertificate(rand.Reader, template, template, public, private)
	if err != nil {
		t.Fatal(err)
	}
	certificate, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return certificate, private
}

func newCredentialFixture(t *testing.T) *credentialFixture {
	t.Helper()
	now := time.Date(2026, 9, 9, 12, 0, 0, 0, time.UTC)
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = state.Close() })
	manager, err := identity.NewManager(state, "https://connect.example.test", []string{"dash"})
	if err != nil {
		t.Fatal(err)
	}
	_, josePrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	public, _, err := identity.PublicKey(josePrivate)
	if err != nil {
		t.Fatal(err)
	}
	raw, invitation, err := manager.Invite("connector-a", "connector", []string{"dash"}, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	claims, err := identity.NewAssertion(invitation.Installation, "https://connect.example.test/enroll", invitation.Role, "", invitation.Epoch, identity.EnrollmentNonce(raw), 0, now)
	if err != nil {
		t.Fatal(err)
	}
	proof, err := identity.Sign(josePrivate, claims)
	if err != nil {
		t.Fatal(err)
	}
	installation, err := manager.Enroll(raw, public, proof)
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.Approve(installation.ID, installation.Fingerprint); err != nil {
		t.Fatal(err)
	}
	ca, caPrivate := credentialCA(t, now)
	issuer, err := New(manager, state, credentialGateway(), ca, caPrivate)
	if err != nil {
		t.Fatal(err)
	}
	return &credentialFixture{issuer: issuer, identity: manager, state: state, now: &now, installation: installation, josePrivate: josePrivate, ca: ca, caPrivate: caPrivate}
}

func snapshot(t *testing.T, fixture *credentialFixture, private ed25519.PrivateKey, installation identity.Installation, sequence int) identity.Installation {
	t.Helper()
	nonce := fmt.Sprintf("%032x", sequence)
	claims, err := identity.NewAssertion(installation.ID, "https://connect.example.test/connector", "connector", "dash", installation.Epoch, nonce, installation.Generation, *fixture.now)
	if err != nil {
		t.Fatal(err)
	}
	proof, err := identity.Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	result, err := fixture.identity.Verify(proof, "connector", "dash", nonce)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func csrFor(t *testing.T, private ed25519.PrivateKey, request x509.CertificateRequest) []byte {
	t.Helper()
	der, err := x509.CreateCertificateRequest(rand.Reader, &request, private)
	if err != nil {
		t.Fatal(err)
	}
	return der
}

func parseLeaf(t *testing.T, certificate Certificate) *x509.Certificate {
	t.Helper()
	leaf, err := x509.ParseCertificate(certificate.DER)
	if err != nil {
		t.Fatal(err)
	}
	return leaf
}

func directLeaf(t *testing.T, fixture *credentialFixture, public ed25519.PublicKey, name string) *x509.Certificate {
	t.Helper()
	template := &x509.Certificate{SerialNumber: big.NewInt(9), Subject: pkix.Name{CommonName: name}, NotBefore: *fixture.now, NotAfter: fixture.now.Add(certificateLifetime), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, DNSNames: []string{name}}
	der, err := x509.CreateCertificate(rand.Reader, template, fixture.ca, public, fixture.caPrivate)
	if err != nil {
		t.Fatal(err)
	}
	leaf, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return leaf
}

func TestIssueBindsDistinctTLSKeyAndIgnoresCSRIdentity(t *testing.T) {
	fixture := newCredentialFixture(t)
	fresh := snapshot(t, fixture, fixture.josePrivate, fixture.installation, 1)
	tlsPrivate, _, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	csr := csrFor(t, tlsPrivate, x509.CertificateRequest{Subject: pkix.Name{CommonName: "evil.example.test"}, DNSNames: []string{"evil.example.test"}, EmailAddresses: []string{"evil@example.test"}})
	issued, err := fixture.issuer.Issue(fresh, "dash", csr)
	if err != nil || !issued.NotAfter.Equal(fixture.now.Add(certificateLifetime)) {
		t.Fatal("certificate issue failed", err)
	}
	leaf := parseLeaf(t, issued)
	name := connectorName(fixture.installation.ID)
	if leaf.Subject.CommonName != name || len(leaf.DNSNames) != 1 || leaf.DNSNames[0] != name || len(leaf.EmailAddresses) != 0 || len(leaf.ExtKeyUsage) != 1 || leaf.ExtKeyUsage[0] != x509.ExtKeyUsageServerAuth {
		t.Fatal("untrusted CSR identity leaked into issued certificate")
	}
	if admitted, err := fixture.issuer.VerifyPeer("dash", leaf); err != nil || admitted.Generation != fresh.Generation || admitted.Fingerprint != fresh.Fingerprint {
		t.Fatal("issued leaf was not bound to current installation", err)
	}
	if _, err := fixture.issuer.Issue(fresh, "dash", csr); err != nil {
		t.Fatal("same-key renewal was rejected", err)
	}
	_, differentCSR, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fixture.issuer.Issue(fresh, "dash", differentCSR); err == nil {
		t.Fatal("new TLS key was accepted without JOSE rotation")
	}
	if _, err := fixture.issuer.Issue(fresh, "dash", csrFor(t, fixture.josePrivate, x509.CertificateRequest{})); err == nil {
		t.Fatal("TLS key reused the installation JOSE key")
	}
	mutated := fresh
	mutated.Fingerprint = "mutated"
	if _, err := fixture.issuer.Issue(mutated, "dash", csr); err == nil {
		t.Fatal("caller-mutated installation snapshot was accepted")
	}
	mutated = fresh
	mutated.Role = "client"
	if _, err := fixture.issuer.Issue(mutated, "dash", csr); err == nil {
		t.Fatal("wrong installation role was accepted")
	}
	if _, err := fixture.issuer.Issue(fresh, "missing", csr); err == nil {
		t.Fatal("unknown connector resource was accepted")
	}
	if _, err := fixture.issuer.Issue(fresh, "dash", []byte("not-a-csr")); err == nil {
		t.Fatal("malformed CSR was accepted")
	}
	broken := append([]byte(nil), csr...)
	broken[len(broken)-1] ^= 1
	if _, err := fixture.issuer.Issue(fresh, "dash", broken); err == nil {
		t.Fatal("unsigned CSR mutation was accepted")
	}
	_, wrongPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	wrongLeaf := directLeaf(t, fixture, wrongPrivate.Public().(ed25519.PublicKey), name)
	if _, err := fixture.issuer.VerifyPeer("dash", wrongLeaf); err == nil {
		t.Fatal("valid-CA leaf with unbound SPKI was accepted")
	}
	wrongName := directLeaf(t, fixture, tlsPrivate.Public().(ed25519.PublicKey), "other.connector.anvil-connect.internal")
	if _, err := fixture.issuer.VerifyPeer("dash", wrongName); err == nil {
		t.Fatal("valid-CA leaf with wrong connector SAN was accepted")
	}
}

func TestPeerBindingFollowsJOSEOverlapRevocationAndReset(t *testing.T) {
	fixture := newCredentialFixture(t)
	oldSnapshot := snapshot(t, fixture, fixture.josePrivate, fixture.installation, 1)
	_, oldCSR, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	oldCertificate, err := fixture.issuer.Issue(oldSnapshot, "dash", oldCSR)
	if err != nil {
		t.Fatal(err)
	}
	oldLeaf := parseLeaf(t, oldCertificate)
	_, newJOSE, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	newPublic, newFingerprint, err := identity.PublicKey(newJOSE)
	if err != nil {
		t.Fatal(err)
	}
	challenge := strings.Repeat("a", 32)
	oldProofClaims, err := identity.NewRotationAssertion(fixture.installation.ID, "https://connect.example.test/rotate", fixture.installation.Role, fixture.installation.Epoch, challenge, newFingerprint, fixture.installation.Generation, *fixture.now)
	if err != nil {
		t.Fatal(err)
	}
	oldProof, err := identity.Sign(fixture.josePrivate, oldProofClaims)
	if err != nil {
		t.Fatal(err)
	}
	newProofClaims, err := identity.NewRotationAssertion(fixture.installation.ID, "https://connect.example.test/rotate", fixture.installation.Role, fixture.installation.Epoch, challenge, newFingerprint, fixture.installation.Generation, *fixture.now)
	if err != nil {
		t.Fatal(err)
	}
	newProof, err := identity.Sign(newJOSE, newProofClaims)
	if err != nil {
		t.Fatal(err)
	}
	rotated, err := fixture.identity.Rotate(fixture.installation.ID, newPublic, oldProof, newProof, challenge, identity.DefaultRotationOverlap)
	if err != nil {
		t.Fatal(err)
	}
	newSnapshot := snapshot(t, fixture, newJOSE, rotated, 2)
	// Reuse the TLS key on purpose. The issuer must bind each leaf to the
	// generation that issued it, rather than treating the key alone as current.
	newCertificate, err := fixture.issuer.Issue(newSnapshot, "dash", oldCSR)
	if err != nil {
		t.Fatal(err)
	}
	newLeaf := parseLeaf(t, newCertificate)
	if string(oldLeaf.Raw) == string(newLeaf.Raw) {
		t.Fatal("rotation did not produce a distinct generation-bound certificate")
	}
	_, staleNewCSR, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fixture.issuer.Issue(oldSnapshot, "dash", staleNewCSR); err == nil {
		t.Fatal("stale JOSE generation replaced a current TLS binding")
	}
	if _, err := fixture.issuer.VerifyPeer("dash", oldLeaf); err != nil {
		t.Fatal("previous key was not accepted during bounded overlap", err)
	}
	if _, err := fixture.issuer.VerifyPeer("dash", newLeaf); err != nil {
		t.Fatal("current key was not accepted", err)
	}
	*fixture.now = rotated.PreviousUntil
	if _, err := fixture.issuer.VerifyPeer("dash", oldLeaf); err == nil {
		t.Fatal("previous TLS binding remained valid after identity overlap")
	}
	if _, err := fixture.issuer.VerifyPeer("dash", newLeaf); err != nil {
		t.Fatal(err)
	}
	if err := fixture.identity.Revoke(rotated.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := fixture.issuer.VerifyPeer("dash", newLeaf); err == nil {
		t.Fatal("revoked connector certificate was accepted")
	}

	fixture = newCredentialFixture(t)
	fresh := snapshot(t, fixture, fixture.josePrivate, fixture.installation, 1)
	_, csr, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	certificate, err := fixture.issuer.Issue(fresh, "dash", csr)
	if err != nil {
		t.Fatal(err)
	}
	if err := fixture.state.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if _, err := fixture.issuer.VerifyPeer("dash", parseLeaf(t, certificate)); err == nil {
		t.Fatal("authority reset reactivated a bound connector certificate")
	}
}

func TestOldLeafCannotInheritReusedTLSKeyAfterEpochReset(t *testing.T) {
	fixture := newCredentialFixture(t)
	oldSnapshot := snapshot(t, fixture, fixture.josePrivate, fixture.installation, 1)
	_, csr, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	oldCertificate, err := fixture.issuer.Issue(oldSnapshot, "dash", csr)
	if err != nil {
		t.Fatal(err)
	}
	oldLeaf := parseLeaf(t, oldCertificate)

	_, newJOSE, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	newPublic, newFingerprint, err := identity.PublicKey(newJOSE)
	if err != nil {
		t.Fatal(err)
	}
	challenge := strings.Repeat("b", 32)
	oldClaims, err := identity.NewRotationAssertion(fixture.installation.ID, "https://connect.example.test/rotate", fixture.installation.Role, fixture.installation.Epoch, challenge, newFingerprint, fixture.installation.Generation, *fixture.now)
	if err != nil {
		t.Fatal(err)
	}
	oldProof, err := identity.Sign(fixture.josePrivate, oldClaims)
	if err != nil {
		t.Fatal(err)
	}
	newClaims, err := identity.NewRotationAssertion(fixture.installation.ID, "https://connect.example.test/rotate", fixture.installation.Role, fixture.installation.Epoch, challenge, newFingerprint, fixture.installation.Generation, *fixture.now)
	if err != nil {
		t.Fatal(err)
	}
	newProof, err := identity.Sign(newJOSE, newClaims)
	if err != nil {
		t.Fatal(err)
	}
	rotated, err := fixture.identity.Rotate(fixture.installation.ID, newPublic, oldProof, newProof, challenge, identity.DefaultRotationOverlap)
	if err != nil {
		t.Fatal(err)
	}
	newSnapshot := snapshot(t, fixture, newJOSE, rotated, 2)
	newCertificate, err := fixture.issuer.Issue(newSnapshot, "dash", csr)
	if err != nil {
		t.Fatal(err)
	}
	newLeaf := parseLeaf(t, newCertificate)
	admittedOld, err := fixture.issuer.VerifyPeer("dash", oldLeaf)
	if err != nil || admittedOld.Generation != oldSnapshot.Generation || admittedOld.Epoch != oldSnapshot.Epoch || admittedOld.Fingerprint != oldSnapshot.Fingerprint {
		t.Fatal("old generation leaf inherited the current binding", err)
	}
	if _, err := fixture.issuer.VerifyPeer("dash", newLeaf); err != nil {
		t.Fatal("new generation leaf was not matched to the current binding", err)
	}
	if err := fixture.state.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if _, err := fixture.issuer.VerifyPeer("dash", oldLeaf); err == nil {
		t.Fatal("old leaf inherited a current generation after epoch reset")
	}
	if _, err := fixture.issuer.VerifyPeer("dash", newLeaf); err == nil {
		t.Fatal("current leaf survived authority reset")
	}
}

func TestNewReparsesCAAndCopiesResources(t *testing.T) {
	fixture := newCredentialFixture(t)
	ca, caPrivate := credentialCA(t, *fixture.now)
	_, wrongPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	// The parsed fields are caller-mutable; only the DER is the CA identity.
	ca.PublicKey = wrongPrivate.Public()
	gateway := credentialGateway()
	issuer, err := New(fixture.identity, fixture.state, gateway, ca, caPrivate)
	if err != nil {
		t.Fatal("constructor trusted mutable parsed CA fields", err)
	}
	gateway.Resources[0].Rule.Methods[0] = "POST"
	if issuer.resources["dash"].Rule.Methods[0] != "GET" {
		t.Fatal("constructor retained caller-owned resource slice")
	}
	if issuer.ca == ca {
		// Retain an explicit pointer check to guard against future removal of
		// the owned reparse.
		t.Fatal("constructor retained caller certificate")
	}
	ca.Raw[0] ^= 1
	fresh := snapshot(t, fixture, fixture.josePrivate, fixture.installation, 1)
	_, csr, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := issuer.Issue(fresh, "dash", csr); err != nil {
		t.Fatal("mutating the caller CA after construction changed issuance", err)
	}
}
