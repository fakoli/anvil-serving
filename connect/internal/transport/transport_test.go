package transport

import (
	"crypto/tls"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/browseridentity"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/testidentity"
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
	g := declaration(t)
	authority := testidentity.New(t, g, ca)
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
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{authority.Certificates["router"]}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
	server.EnableHTTP2 = true
	server.StartTLS()
	defer server.Close()
	g.Resources[0].TunnelAddress = server.Listener.Addr().String()
	d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true), authority.Issuer)
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
			authority := testidentity.New(t, g, ca)
			g.Resources[0].TunnelAddress = server.Listener.Addr().String()
			d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true), authority.Issuer)
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
	authority := testidentity.New(t, g, ca)
	mismatched := ca.Leaf(t, GatewayPeer, true)
	mismatched.PrivateKey = ca.Leaf(t, GatewayPeer, true).PrivateKey
	for _, cert := range []tls.Certificate{{}, mismatched, ca.Leaf(t, "*.anvil-connect.internal", true), ca.Leaf(t, GatewayPeer, true, "other.anvil-connect.internal"), ca.Leaf(t, "other.anvil-connect.internal", true), ca.Leaf(t, GatewayPeer, false), testpki.New(t).Leaf(t, GatewayPeer, true)} {
		if d, err := NewDispatcher(g, ca.Roots, cert, authority.Issuer); err == nil {
			d.Close()
			t.Fatal("invalid client identity accepted")
		}
	}
}

func TestBrowserDispatchInjectsOnlyContextIdentity(t *testing.T) {
	const assertion = "acai1.e30.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
	ca := testpki.New(t)
	g := config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:17890", MaxConcurrent: 2, Resources: []config.Resource{{Rule: config.Rule{ID: "dash", Host: "dash.example.test", PathPrefix: "/", Methods: []string{"GET"}, Access: "browser", NativeAuth: "signed-identity", Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 10}}, Connector: "origin-a", TunnelAddress: "127.0.0.1:17891", IdentityKeyEnv: "ANVIL_CONNECT_DASH_IDENTITY_KEY", IdentityKeyID: "dash-v1"}}}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	g.Resources[0].TunnelAddress = listener.Addr().String()
	authority := testidentity.New(t, g, ca)
	var calls atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get(browseridentity.Header) != assertion || len(r.Header.Values(browseridentity.Header)) != 1 || r.Header.Get("X-Forwarded-Host") != "" {
			t.Error("dispatcher did not inject exactly its context identity")
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{authority.Certificates["dash"]}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
	server.EnableHTTP2 = true
	server.Listener = listener
	server.StartTLS()
	defer server.Close()
	d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true), authority.Issuer)
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	request := httptest.NewRequest(http.MethodGet, "http://dash.example.test/", nil)
	request.Host = "dash.example.test"
	request.RequestURI = "/"
	request.URL.Scheme, request.URL.Host = "", ""
	request.Header.Set(browseridentity.Header, "forged")
	request.Header.Set("X-Forwarded-Host", "forged.example.test")
	request = request.WithContext(browseridentity.WithAssertion(request.Context(), assertion))
	if !g.Resources[0].Rule.Allows(request.Host, request.URL.Path, request.Method) {
		t.Fatal("invalid signed browser test request")
	}
	response := httptest.NewRecorder()
	d.BrowserDispatch(response, request, g.Resources[0], session.Admission{Resource: "dash", Host: "dash.example.test"})
	if response.Code != http.StatusNoContent || calls.Load() != 1 {
		t.Fatalf("context identity was not dispatched: status=%d body=%q calls=%d", response.Code, response.Body.String(), calls.Load())
	}
	response = httptest.NewRecorder()
	request = httptest.NewRequest(http.MethodGet, "http://dash.example.test/", nil)
	request.Host = "dash.example.test"
	request.RequestURI = "/"
	request.URL.Scheme, request.URL.Host = "", ""
	d.BrowserDispatch(response, request, g.Resources[0], session.Admission{Resource: "dash", Host: "dash.example.test"})
	if response.Code != http.StatusForbidden || calls.Load() != 1 {
		t.Fatal("caller header substituted for signed context identity")
	}
}
