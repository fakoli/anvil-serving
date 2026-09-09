package credential

import (
	"crypto/ed25519"
	"crypto/rand"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func TestVerifyPeerUsesOnlySignedDERFields(t *testing.T) {
	f := newCredentialFixture(t)
	fresh := snapshot(t, f, f.josePrivate, f.installation, 1)
	_, csr, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	issued, err := f.issuer.Issue(fresh, "dash", csr)
	if err != nil {
		t.Fatal(err)
	}
	bound := parseLeaf(t, issued)
	otherKey, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	wrong := directLeaf(t, f, otherKey, "unbound.example.test")
	// Reproduce the split representation: a real CA signature authenticates
	// one leaf, while all application-visible parsed fields describe another.
	forged := *bound
	forged.Raw = wrong.Raw
	forged.RawTBSCertificate = wrong.RawTBSCertificate
	forged.Signature = wrong.Signature
	forged.SignatureAlgorithm = wrong.SignatureAlgorithm
	if _, err := f.issuer.VerifyPeer("dash", &forged); err == nil {
		t.Fatal("unsigned parsed certificate fields became authority")
	}
	forged = *bound
	forged.Raw = nil
	if _, err := f.issuer.VerifyPeer("dash", &forged); err == nil {
		t.Fatal("leaf without DER accepted")
	}
	forged = *bound
	forged.Raw = []byte("invalid certificate")
	if _, err := f.issuer.VerifyPeer("dash", &forged); err == nil {
		t.Fatal("malformed signed representation accepted")
	}
}

func TestFailedIssuanceCleanupPreservesConcurrentNewerBinding(t *testing.T) {
	f := newCredentialFixture(t)
	fresh := snapshot(t, f, f.josePrivate, f.installation, 1)
	_, csr, err := GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.issuer.Issue(fresh, "dash", csr); err != nil {
		t.Fatal(err)
	}
	var staleWrite bindingRecord
	if err := f.state.View(func(tx *store.Tx) error { return tx.Get("installations", recordKey("dash"), &staleWrite) }); err != nil {
		t.Fatal(err)
	}
	// A later successful renewal writes a newer expiry while the first caller
	// could still be awaiting its final identity check.
	*f.now = f.now.Add(time.Second)
	newer, err := f.issuer.Issue(fresh, "dash", csr)
	if err != nil {
		t.Fatal(err)
	}
	f.issuer.restore("dash", nil, staleWrite)
	if _, err := f.issuer.VerifyPeer("dash", parseLeaf(t, newer)); err != nil {
		t.Fatal("stale cleanup removed newer successful authority", err)
	}
	var current bindingRecord
	if err := f.state.View(func(tx *store.Tx) error { return tx.Get("installations", recordKey("dash"), &current) }); err != nil || !current.Current.NotAfter.Equal(newer.NotAfter) {
		t.Fatal("newer write did not survive cleanup", err)
	}
}
