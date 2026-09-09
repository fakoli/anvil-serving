package transport

import (
	"crypto/tls"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

func declaration(t *testing.T) config.Gateway {
	t.Helper()
	f, err := os.Open("../../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	g, err := config.ReadGateway(f)
	if err != nil {
		t.Fatal(err)
	}
	return g
}

func TestDispatchHasFixedTargetAndVerifiedInnerTLS(t *testing.T) {
	ca := testpki.New(t)
	var calls atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if len(r.TLS.VerifiedChains) == 0 || r.TLS.PeerCertificates[0].VerifyHostname(GatewayPeer) != nil {
			t.Error("missing gateway identity")
		}
		if r.Host != "api.example.test" || r.Header.Get(origin.ResourceHeader) != "router" || r.Header.Get("Authorization") != "" || r.Header.Get("X-Api-Key") != "" || r.Header.Get("X-Forwarded-Host") != "" {
			t.Error("incorrect inner request envelope")
		}
		_, _ = io.WriteString(w, "authenticated connector")
	}))
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, ConnectorPeer("origin-a"), false)}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
	server.EnableHTTP2 = true
	server.StartTLS()
	defer server.Close()
	g := declaration(t)
	g.Resources[0].TunnelAddress = server.Listener.Addr().String()
	d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	r := httptest.NewRequest("GET", "http://api.example.test/v1/models", nil)
	r.Header.Set("Authorization", "Bearer must-not-cross-hop")
	r.Header.Set("X-Api-Key", "must-not-cross-hop")
	r.Header.Set("X-Forwarded-Host", "admin.example.test")
	r.RequestURI = "/v1/models"
	r.URL.Scheme, r.URL.Host = "", ""
	a := access.Admission{Resource: "router", Method: "GET"}
	w := httptest.NewRecorder()
	d.Dispatch(w, r, g.Resources[0], a)
	if w.Code != 200 || w.Body.String() != "authenticated connector" || calls.Load() != 1 {
		t.Fatal("inner dispatch failed", w.Code)
	}
	bad := g.Resources[0]
	bad.TunnelAddress = "127.0.0.1:1"
	w = httptest.NewRecorder()
	d.Dispatch(w, r, bad, a)
	if w.Code != 403 || calls.Load() != 1 {
		t.Fatal("caller expanded tunnel destination")
	}
	wrong := a
	wrong.Resource = "dashboard"
	w = httptest.NewRecorder()
	d.Dispatch(w, r, g.Resources[0], wrong)
	if w.Code != 403 || calls.Load() != 1 {
		t.Fatal("admission crossed resource")
	}
}

func TestInnerServerIdentityCannotBeSubstituted(t *testing.T) {
	for _, kind := range []string{"wrong-name", "untrusted", "expired", "client-only", "wildcard", "multi-san"} {
		t.Run(kind, func(t *testing.T) {
			ca := testpki.New(t)
			cert := ca.Leaf(t, "other.connector.anvil-connect.internal", false)
			switch kind {
			case "wildcard":
				cert = ca.Leaf(t, "*.connector.anvil-connect.internal", false)
			case "multi-san":
				cert = ca.Leaf(t, ConnectorPeer("origin-a"), false, ConnectorPeer("origin-b"))
			case "untrusted":
				cert = testpki.New(t).Leaf(t, ConnectorPeer("origin-a"), false)
			case "client-only":
				cert = ca.Leaf(t, ConnectorPeer("origin-a"), true)
			case "expired":
				// A trusted certificate for a different name is separately covered;
				// use a client clock after the fixture certificate's NotAfter here.
				cert = ca.Leaf(t, ConnectorPeer("origin-a"), false)
			}
			var calls atomic.Int32
			server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
			server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{cert}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
			server.EnableHTTP2 = true
			server.StartTLS()
			defer server.Close()
			g := declaration(t)
			g.Resources[0].TunnelAddress = server.Listener.Addr().String()
			d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true))
			if err != nil {
				t.Fatal(err)
			}
			defer d.Close()
			if kind == "expired" {
				d.resources["router"].transport.TLSClientConfig.Time = func() time.Time { return time.Now().Add(2 * time.Hour) }
			}
			r := httptest.NewRequest("GET", "http://api.example.test/v1/models", nil)
			r.RequestURI = "/v1/models"
			r.URL.Scheme, r.URL.Host = "", ""
			w := httptest.NewRecorder()
			d.Dispatch(w, r, g.Resources[0], access.Admission{Resource: "router", Method: "GET"})
			if w.Code != 502 || calls.Load() != 0 {
				t.Fatal("substituted connector accepted", w.Code)
			}
			if strings.Contains(w.Body.String(), "certificate") || strings.Contains(w.Body.String(), "127.0.0.1") {
				t.Fatal("internal TLS details leaked")
			}
		})
	}
}

func TestDispatcherRejectsMissingOrWrongGatewayIdentity(t *testing.T) {
	ca := testpki.New(t)
	g := declaration(t)
	mismatched := ca.Leaf(t, GatewayPeer, true)
	mismatched.PrivateKey = ca.Leaf(t, GatewayPeer, true).PrivateKey
	for _, cert := range []tls.Certificate{{}, mismatched, ca.Leaf(t, "*.anvil-connect.internal", true), ca.Leaf(t, GatewayPeer, true, "other.anvil-connect.internal"), ca.Leaf(t, "other.anvil-connect.internal", true), ca.Leaf(t, GatewayPeer, false), testpki.New(t).Leaf(t, GatewayPeer, true)} {
		if d, err := NewDispatcher(g, ca.Roots, cert); err == nil {
			d.Close()
			t.Fatal("invalid client identity accepted")
		}
	}
}
