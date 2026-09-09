package control

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/credential"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

type clientFixture struct {
	server       *httptest.Server
	control      *Server
	client       *Client
	installation identity.Installation
	private      ed25519.PrivateKey
}

func newClientFixture(t *testing.T) clientFixture {
	t.Helper()
	f := newFixture(t)
	installation := f.installation
	installation.Resources = []string{"router"}
	ca := testpki.New(t)
	server := httptest.NewUnstartedServer(f.server)
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, "connect.example.test", false)}}
	server.EnableHTTP2 = true
	server.StartTLS()
	t.Cleanup(server.Close)
	client, err := testClient("https://connect.example.test", installation, f.private, ca.Roots, server.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.Close)
	return clientFixture{server, f.server, client, installation, f.private}
}

func testClient(gateway string, installation identity.Installation, private ed25519.PrivateKey, roots *x509.CertPool, target string) (*Client, error) {
	dialer := &net.Dialer{Timeout: time.Second}
	return newClient(gateway, installation, private, ClientOptions{RootCAs: roots}, func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != "connect.example.test:443" {
			return nil, errors.New("control target escaped fixed host")
		}
		return dialer.DialContext(ctx, "tcp", target)
	})
}

func TestClientRenewUsesCurrentTLSServerAndReturnsConservativeStart(t *testing.T) {
	f := newClientFixture(t)
	_, csr, err := credential.GenerateCSR()
	if err != nil {
		t.Fatal(err)
	}
	before := time.Now()
	response, started, err := f.client.Renew(context.Background(), "router", csr)
	if started == started.Round(0) {
		t.Fatal("request-start anchor lost its monotonic clock reading")
	}
	if err != nil {
		t.Fatal("renewal through TLS control server failed", err)
	}
	if started.Before(before) || started.After(time.Now()) || response.Schema != Schema || response.Installation != f.installation.ID || response.Resource != "router" || response.Epoch != f.installation.Epoch || response.Generation != f.installation.Generation || response.LeaseMilliseconds < 1 || response.LeaseMilliseconds > 45000 || response.Certificate == "" {
		t.Fatal("renewal response was not locally bound and bounded")
	}
	if _, err := f.control.leases.Authenticate(response.TransportToken); err != nil {
		t.Fatal("renewal did not create usable transport lease", err)
	}
}

func TestClientRotateReturnsNextSnapshotWithoutMutatingCaller(t *testing.T) {
	f := newClientFixture(t)
	_, nextPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	nextPublic, nextFingerprint, err := identity.PublicKey(nextPrivate)
	if err != nil {
		t.Fatal(err)
	}
	response, err := f.client.Rotate(context.Background(), "router", nextPublic, nextPrivate, time.Minute)
	if err != nil || response.Schema != Schema || response.Installation != f.installation.ID || response.Generation != f.installation.Generation+1 || response.Fingerprint != nextFingerprint {
		t.Fatal("rotation response did not bind the proposed key", err)
	}
	if f.client.installation.Generation != f.installation.Generation || f.client.installation.Fingerprint != f.installation.Fingerprint || string(f.client.private) != string(f.private) {
		t.Fatal("rotation mutated live control client state")
	}
}

func TestClientRejectsRevokedInstallationBeforeRenewal(t *testing.T) {
	f := newClientFixture(t)
	if err := f.control.identity.Revoke(f.installation.ID); err != nil {
		t.Fatal(err)
	}
	if _, _, err := f.client.Renew(context.Background(), "router", nil); err == nil {
		t.Fatal("revoked installation renewed through client")
	}
}

func TestClientRejectsTLSRedirectAndMalformedBoundResponses(t *testing.T) {
	base := newFixture(t)
	installation := base.installation
	installation.Resources = []string{"router"}
	for name, handler := range map[string]http.Handler{
		"redirect": http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "https://other.example.test/v1/challenge", http.StatusFound)
		}),
		"bad-nonce": http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(ChallengeResponse{Schema: Schema, Nonce: "not-a-nonce"})
		}),
	} {
		t.Run(name, func(t *testing.T) {
			ca := testpki.New(t)
			server := httptest.NewUnstartedServer(handler)
			server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, "connect.example.test", false)}}
			server.EnableHTTP2 = true
			server.StartTLS()
			defer server.Close()
			client, err := testClient("https://connect.example.test", installation, base.private, ca.Roots, server.Listener.Addr().String())
			if err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			if _, _, err := client.Renew(context.Background(), "router", nil); err == nil {
				t.Fatal("unbound control response accepted")
			}
		})
	}

	ca := testpki.New(t)
	server := httptest.NewUnstartedServer(http.NotFoundHandler())
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, "connect.example.test", false)}}
	server.StartTLS()
	defer server.Close()
	wrongRoots := testpki.New(t).Roots
	client, err := testClient("https://connect.example.test", installation, base.private, wrongRoots, server.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if _, _, err := client.Renew(context.Background(), "router", nil); err == nil {
		t.Fatal("untrusted TLS control server accepted")
	}
}

func TestClientConstructorAndResponseBindingsFailClosed(t *testing.T) {
	f := newFixture(t)
	installation := f.installation
	installation.Resources = []string{"router"}
	for _, gateway := range []string{"http://connect.example.test", "https://connect.example.test/", "https://connect.example.test:443", "https://connect.example.test?x=1"} {
		if _, err := NewClient(gateway, installation, f.private, ClientOptions{}); err == nil {
			t.Fatal("non-exact control origin accepted", gateway)
		}
	}
	mutated := installation
	mutated.Fingerprint = "changed"
	if _, err := NewClient("https://connect.example.test", mutated, f.private, ClientOptions{}); err == nil {
		t.Fatal("caller-mutated installation snapshot accepted")
	}
	if _, err := NewClient("https://connect.example.test", installation, f.private, ClientOptions{HTTPProxyURL: "http://user:pass@proxy.example.test"}); err == nil {
		t.Fatal("credential-bearing proxy accepted")
	}
}
