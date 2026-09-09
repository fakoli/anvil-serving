// Package testidentity builds real, ephemeral installation and TLS authority
// for integration tests. It never reads operator identity or trust material.
package testidentity

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"encoding/hex"
	"path/filepath"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/credential"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

type Fixture struct {
	State         *store.Store
	Identity      *identity.Manager
	Issuer        *credential.Issuer
	Certificates  map[string]tls.Certificate
	Installations map[string]identity.Installation
}

func New(t *testing.T, gateway config.Gateway, ca testpki.Authority) Fixture {
	t.Helper()
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = state.Close() })
	resources := []string{}
	groups := map[string][]string{}
	for _, r := range gateway.Resources {
		resources = append(resources, r.Rule.ID)
		groups[r.Connector] = append(groups[r.Connector], r.Rule.ID)
	}
	const audience = "https://connect.example.test"
	manager, err := identity.NewManager(state, audience, resources)
	if err != nil {
		t.Fatal(err)
	}
	root, signer := ca.SigningIdentity(t)
	issuer, err := credential.New(manager, state, gateway, root, signer)
	if err != nil {
		t.Fatal(err)
	}
	f := Fixture{state, manager, issuer, map[string]tls.Certificate{}, map[string]identity.Installation{}}
	for id, resources := range groups {
		_, private, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		public, _, err := identity.PublicKey(private)
		if err != nil {
			t.Fatal(err)
		}
		raw, invitation, err := manager.Invite(id, "connector", resources, time.Minute)
		if err != nil {
			t.Fatal(err)
		}
		claims, err := identity.NewAssertion(id, audience+"/enroll", "connector", "", invitation.Epoch, identity.EnrollmentNonce(raw), 0, time.Now())
		if err != nil {
			t.Fatal(err)
		}
		proof, err := identity.Sign(private, claims)
		if err != nil {
			t.Fatal(err)
		}
		installation, err := manager.Enroll(raw, public, proof)
		if err != nil {
			t.Fatal(err)
		}
		if err := manager.Approve(id, installation.Fingerprint); err != nil {
			t.Fatal(err)
		}
		for _, resource := range resources {
			var random [32]byte
			if _, err := rand.Read(random[:]); err != nil {
				t.Fatal(err)
			}
			nonce := hex.EncodeToString(random[:])
			claims, err := identity.NewAssertion(id, audience+"/connector", "connector", resource, installation.Epoch, nonce, installation.Generation, time.Now())
			if err != nil {
				t.Fatal(err)
			}
			proof, err := identity.Sign(private, claims)
			if err != nil {
				t.Fatal(err)
			}
			verified, err := manager.Verify(proof, "connector", resource, nonce)
			if err != nil {
				t.Fatal(err)
			}
			key, csr, err := credential.GenerateCSR()
			if err != nil {
				t.Fatal(err)
			}
			certificate, err := issuer.Issue(verified, resource, csr)
			if err != nil {
				t.Fatal(err)
			}
			f.Certificates[resource] = tls.Certificate{Certificate: [][]byte{certificate.DER}, PrivateKey: key}
			f.Installations[id] = verified
		}
	}
	return f
}
