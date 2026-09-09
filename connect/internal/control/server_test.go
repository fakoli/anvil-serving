package control

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/credential"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
	"github.com/fakoli/anvil-serving/connect/internal/tunnelgate"
)

type fixture struct {
	server       *Server
	manager      *identity.Manager
	issuer       *credential.Issuer
	installation identity.Installation
	private      ed25519.PrivateKey
	now          *time.Time
}

func newFixture(t *testing.T) fixture {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { state.Close() })
	f, err := os.Open("../../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	g, err := config.ReadGateway(f)
	f.Close()
	if err != nil {
		t.Fatal(err)
	}
	m, err := identity.NewManager(state, "https://connect.example.test", []string{"router"})
	if err != nil {
		t.Fatal(err)
	}
	ca := testpki.New(t)
	caCert, caKey := ca.SigningIdentity(t)
	issuer, err := credential.New(m, state, g, caCert, caKey)
	if err != nil {
		t.Fatal(err)
	}
	leases, err := tunnelgate.NewLeases(g, m, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	server, err := NewServer("connect.example.test", g, m, issuer, leases, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	raw, invitation, err := m.Invite("origin-a", "connector", []string{"router"}, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	public, fingerprint, err := identity.PublicKey(private)
	if err != nil {
		t.Fatal(err)
	}
	claims, err := identity.NewAssertion("origin-a", "https://connect.example.test/enroll", "connector", "", invitation.Epoch, identity.EnrollmentNonce(raw), 0, now)
	if err != nil {
		t.Fatal(err)
	}
	proof, err := identity.Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	response := post(t, server, EnrollmentPath, EnrollmentRequest{raw, encoded(public), proof})
	if response.Code != 200 {
		t.Fatal("enrollment failed", response.Code)
	}
	var enrolled InstallationResponse
	if decode(response.Body, &enrolled) != nil || enrolled.Status != "pending" || enrolled.Fingerprint != fingerprint || enrolled.Epoch != invitation.Epoch {
		t.Fatal("enrollment response invalid")
	}
	if replay := post(t, server, EnrollmentPath, EnrollmentRequest{raw, encoded(public), proof}); replay.Code != 403 {
		t.Fatal("invitation replay accepted")
	}
	if err := m.Approve(enrolled.Installation, enrolled.Fingerprint); err != nil {
		t.Fatal(err)
	}
	installation := identity.Installation{ID: enrolled.Installation, Role: enrolled.Role, PublicKey: public, Fingerprint: enrolled.Fingerprint, Epoch: enrolled.Epoch, Generation: enrolled.Generation}
	return fixture{server, m, issuer, installation, private, &now}
}

func post(t *testing.T, server *Server, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest("POST", path, bytes.NewReader(data))
	r.Host = "connect.example.test"
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	server.ServeHTTP(w, r)
	return w
}

func (f fixture) proof(t *testing.T, audience, nonce, digest string) string {
	t.Helper()
	claims, err := identity.NewAssertion(f.installation.ID, "https://connect.example.test/"+audience, "connector", "router", f.installation.Epoch, nonce, f.installation.Generation, *f.now)
	if err != nil {
		t.Fatal(err)
	}
	claims.Fingerprint = digest
	proof, err := identity.Sign(f.private, claims)
	if err != nil {
		t.Fatal(err)
	}
	return proof
}

func (f fixture) challenge(t *testing.T, purpose, digest string) string {
	t.Helper()
	var random [32]byte
	if _, err := rand.Read(random[:]); err != nil {
		t.Fatal(err)
	}
	proof := f.proof(t, "challenge", hex.EncodeToString(random[:]), digest)
	w := post(t, f.server, ChallengePath, ChallengeRequest{Installation: f.installation.ID, Resource: "router", Purpose: purpose, Digest: digest, Proof: proof})
	var response ChallengeResponse
	if w.Code != 200 || decode(w.Body, &response) != nil || response.Schema != Schema || !validDigest(response.Nonce) {
		t.Fatal("challenge denied", w.Code)
	}
	return response.Nonce
}

func (f fixture) renewal(t *testing.T, csr []byte) RenewalRequest {
	t.Helper()
	nonce := f.challenge(t, "renew", RenewalDigest(csr))
	return RenewalRequest{Installation: f.installation.ID, Resource: "router", Nonce: nonce, Proof: f.proof(t, "connector", nonce, ""), CSR: encoded(csr)}
}

func TestVerifiedRenewalIssuesBoundCertificateAndTransportLease(t *testing.T) {
	f := newFixture(t)
	_, csr, err := credential.GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	request := f.renewal(t, csr)
	w := post(t, f.server, RenewalPath, request)
	var renewed RenewalResponse
	if w.Code != 200 || decode(w.Body, &renewed) != nil {
		t.Fatal("renewal failed", w.Code)
	}
	if renewed.Schema != Schema || renewed.Installation != f.installation.ID || renewed.Epoch != f.installation.Epoch || renewed.Generation != f.installation.Generation || renewed.LeaseMilliseconds != 45000 {
		t.Fatal("renewal context changed")
	}
	der, err := decoded(renewed.Certificate, 16384)
	if err != nil {
		t.Fatal(err)
	}
	leaf, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	if verified, err := f.issuer.VerifyPeer("router", leaf); err != nil || verified.Generation != f.installation.Generation {
		t.Fatal("certificate not bound to verified installation", err)
	}
	if _, err := f.server.leases.Authenticate(renewed.TransportToken); err != nil {
		t.Fatal("verified renewal token denied", err)
	}
	if replay := post(t, f.server, RenewalPath, request); replay.Code != 403 {
		t.Fatal("control challenge replay admitted")
	}
	if err := f.manager.Revoke(f.installation.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := f.server.leases.Authenticate(renewed.TransportToken); err == nil {
		t.Fatal("revocation left transport token")
	}
	if _, err := f.issuer.VerifyPeer("router", leaf); err == nil {
		t.Fatal("revocation left inner TLS key")
	}
}

func TestChallengeIsPossessionProtectedAndBindsExactCSR(t *testing.T) {
	f := newFixture(t)
	unauth := post(t, f.server, ChallengePath, ChallengeRequest{Installation: "origin-a", Resource: "router", Purpose: "renew", Digest: RenewalDigest(nil)})
	if unauth.Code != 403 || len(f.server.challenges) != 0 {
		t.Fatal("unauthenticated caller reserved challenge capacity")
	}
	_, csr, _ := credential.GenerateCSR()
	request := f.renewal(t, csr)
	_, otherCSR, _ := credential.GenerateCSR()
	tampered := request
	tampered.CSR = encoded(otherCSR)
	if w := post(t, f.server, RenewalPath, tampered); w.Code != 403 {
		t.Fatal("proof moved to another TLS key")
	}
	tampered = request
	tampered.Resource = "other"
	if w := post(t, f.server, RenewalPath, tampered); w.Code != 403 {
		t.Fatal("proof moved to another resource")
	}
	if w := post(t, f.server, RenewalPath, request); w.Code != 200 {
		t.Fatal("mismatched request burned legitimate bound challenge")
	}
}

func TestOldGenerationRenewalCannotExtendOverlap(t *testing.T) {
	f := newFixture(t)
	_, newPrivate, _ := ed25519.GenerateKey(rand.Reader)
	newPublic, newFingerprint, _ := identity.PublicKey(newPrivate)
	nonce := f.challenge(t, "rotate", RotationDigest(newPublic, 60000))
	claims, err := identity.NewRotationAssertion(f.installation.ID, "https://connect.example.test/rotate", "connector", f.installation.Epoch, nonce, newFingerprint, f.installation.Generation, *f.now)
	if err != nil {
		t.Fatal(err)
	}
	oldProof, _ := identity.Sign(f.private, claims)
	claims, err = identity.NewRotationAssertion(f.installation.ID, "https://connect.example.test/rotate", "connector", f.installation.Epoch, nonce, newFingerprint, f.installation.Generation, *f.now)
	if err != nil {
		t.Fatal(err)
	}
	newProof, _ := identity.Sign(newPrivate, claims)
	rotated := post(t, f.server, RotationPath, RotationRequest{f.installation.ID, "router", nonce, encoded(newPublic), oldProof, newProof, 60000})
	if rotated.Code != 200 {
		t.Fatal("rotation failed", rotated.Code)
	}
	*f.now = f.now.Add(57 * time.Second)
	response := post(t, f.server, RenewalPath, f.renewal(t, nil))
	var renewed RenewalResponse
	if response.Code != 200 || decode(response.Body, &renewed) != nil || renewed.LeaseMilliseconds != 3000 {
		t.Fatal("old generation renewal exceeded overlap", response.Code, renewed.LeaseMilliseconds)
	}
	binding := access.LeaseBinding{Installation: renewed.Installation, Resource: renewed.Resource, Epoch: renewed.Epoch, Generation: renewed.Generation}
	lease, err := access.NewLease(binding, func() time.Time { return *f.now })
	if err != nil {
		t.Fatal(err)
	}
	if err := lease.RenewFor(binding, 1, *f.now, time.Duration(renewed.LeaseMilliseconds)*time.Millisecond); err != nil {
		t.Fatal(err)
	}
	*f.now = f.now.Add(3 * time.Second)
	if lease.Check() == nil {
		t.Fatal("connector retained old generation at overlap expiry")
	}
	if _, err := f.server.leases.Authenticate(renewed.TransportToken); err == nil {
		t.Fatal("gateway retained old generation at overlap expiry")
	}
}

func TestControlRejectsAmbiguousJSONAndBrowserCarriers(t *testing.T) {
	f := newFixture(t)
	for _, body := range []string{`{"installation":"origin-a","installation":"other"}`, `{"Installation":"origin-a"}`, `{"installation":null}`, `{"unknown":true}`, `{} {}`, strings.Repeat("x", maxBody+1)} {
		r := httptest.NewRequest("POST", ChallengePath, strings.NewReader(body))
		r.Host = "connect.example.test"
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		f.server.ServeHTTP(w, r)
		if w.Code != 400 {
			t.Fatal("ambiguous JSON admitted", w.Code)
		}
	}
	for _, name := range []string{"Origin", "Cookie", "Authorization", "Proxy-Authorization", "X-Api-Key", "Upgrade"} {
		r := httptest.NewRequest("POST", ChallengePath, strings.NewReader("{}"))
		r.Host = "connect.example.test"
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set(name, "untrusted")
		w := httptest.NewRecorder()
		f.server.ServeHTTP(w, r)
		if w.Code != 400 {
			t.Fatal("wrong control credential carrier admitted", name, w.Code)
		}
	}
}
