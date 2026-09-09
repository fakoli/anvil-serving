package origin

import (
	"context"
	"crypto/tls"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

type browserOriginFixture struct {
	proxy    *Proxy
	server   *httptest.Server
	client   *http.Client
	ca       testpki.Authority
	envelope config.Envelope
	lease    *access.Lease
}

func browserEnvelope(origin string, nativeAuth string) config.Envelope {
	return config.Envelope{
		Rule: config.Rule{
			ID: "dash", Host: "dash.example.test", PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: nativeAuth,
			Limits: config.Limits{RequestBytes: 1024, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 30},
		},
		Listen: "127.0.0.1:17991", OriginURL: origin,
	}
}

func browserProxyFixture(t *testing.T, handler http.HandlerFunc, nativeAuth string, active bool) browserOriginFixture {
	t.Helper()
	native := httptest.NewServer(handler)
	t.Cleanup(native.Close)
	envelope := browserEnvelope(native.URL, nativeAuth)
	binding := access.LeaseBinding{Installation: "origin-a", Resource: envelope.Rule.ID, Epoch: strings.Repeat("a", 64), Generation: 1}
	lease, err := access.NewLease(binding, nil)
	if err != nil {
		t.Fatal(err)
	}
	if active {
		if err := lease.Renew(binding, 1, time.Now()); err != nil {
			t.Fatal(err)
		}
	}
	p, err := NewBrowser(envelope, gatewayName, lease)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(p.Close)
	ca := testpki.New(t)
	server := httptest.NewUnstartedServer(p)
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, connectorName, false)}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
	server.EnableHTTP2 = true
	server.StartTLS()
	t.Cleanup(func() { server.CloseClientConnections(); server.Close() })
	transport := &http.Transport{DisableKeepAlives: true, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: ca.Roots, ServerName: connectorName, Certificates: []tls.Certificate{ca.Leaf(t, gatewayName, true)}}}
	t.Cleanup(transport.CloseIdleConnections)
	return browserOriginFixture{p, server, &http.Client{Transport: transport, Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, ca, envelope, lease}
}

func (f browserOriginFixture) request(t *testing.T, method, path string) *http.Request {
	t.Helper()
	r, err := http.NewRequest(method, f.server.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	r.Host = f.envelope.Rule.Host
	r.Header.Set(ResourceHeader, f.envelope.Rule.ID)
	return r
}

func TestBrowserProxyPreservesNativeStateAndStripsConnectIdentity(t *testing.T) {
	var dispatched atomic.Int32
	f := browserProxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		dispatched.Add(1)
		if r.Host != "dash.example.test" || r.URL.RequestURI() != "/settings?view=security" || r.Header.Get("Origin") != "https://dash.example.test" {
			t.Error("browser public origin or fixed target changed")
		}
		if r.Header.Get("Cookie") != "native=one" || r.Header.Get("X-CSRF-Token") != "csrf" || r.Header.Get("Authorization") != "Bearer application-token" {
			t.Error("native browser session or csrf state did not survive")
		}
		for _, name := range []string{"X-Anvil-Connect-Resource", "X-Anvil-Connect-Principal", "X-Auth-Request-User", "X-Forwarded-For", "Remote-User"} {
			if r.Header.Get(name) != "" {
				t.Errorf("forged identity header reached native browser app: %s", name)
			}
		}
		w.Header().Add("Set-Cookie", "native-next=two; Path=/; Secure; HttpOnly")
		_, _ = io.WriteString(w, "dashboard")
	}, "passthrough", true)
	r := f.request(t, http.MethodPost, "/settings?view=security")
	r.Header.Set("Origin", "https://dash.example.test")
	r.Header.Set("Cookie", "native=one; __Host-anvil-connect=opaque")
	r.Header.Set("X-CSRF-Token", "csrf")
	r.Header.Set("Authorization", "Bearer application-token")
	r.Header.Set("X-Anvil-Connect-Principal", "forged")
	r.Header.Set("X-Auth-Request-User", "forged")
	r.Header.Set("X-Forwarded-For", "203.0.113.1")
	r.Header.Set("Remote-User", "forged")
	response, err := f.client.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(response.Body)
	response.Body.Close()
	if response.StatusCode != http.StatusOK || string(body) != "dashboard" || response.Header.Get("Set-Cookie") == "" || dispatched.Load() != 1 {
		t.Fatal("browser request was not safely relayed")
	}
}

func TestBrowserProxyResponseConfinement(t *testing.T) {
	for name, responseHeader := range map[string]string{
		"cross-origin-redirect": "https://other.example.test/",
		"escaped-path":          "/%2e%2e/admin",
	} {
		t.Run(name, func(t *testing.T) {
			var dispatched atomic.Int32
			f := browserProxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
				dispatched.Add(1)
				w.Header().Set("Location", responseHeader)
				w.WriteHeader(http.StatusFound)
			}, "none", true)
			response, err := f.client.Do(f.request(t, http.MethodGet, "/"))
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			if response.StatusCode != http.StatusBadGateway || dispatched.Load() != 1 {
				t.Fatal("browser redirect escaped resource")
			}
		})
	}
	var dispatched atomic.Int32
	f := browserProxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		dispatched.Add(1)
		w.Header().Set("Set-Cookie", "native=one; Domain=dash.example.test; Path=/")
		w.WriteHeader(http.StatusOK)
	}, "none", true)
	response, err := f.client.Do(f.request(t, http.MethodGet, "/"))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusBadGateway || dispatched.Load() != 1 {
		t.Fatal("domain browser cookie escaped resource")
	}
}

func TestBrowserProxyDeniesForeignOriginBeforeNativeApp(t *testing.T) {
	var dispatched atomic.Int32
	f := browserProxyFixture(t, func(http.ResponseWriter, *http.Request) { dispatched.Add(1) }, "none", true)
	r := f.request(t, http.MethodPost, "/settings")
	r.Header.Set("Origin", "https://attacker.example.test")
	response, err := f.client.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusForbidden || dispatched.Load() != 0 {
		t.Fatal("foreign browser origin reached native app")
	}
}

func TestBrowserProxyDeniesForeignWebSocketOriginBeforeNativeApp(t *testing.T) {
	var dispatched atomic.Int32
	f := browserProxyFixture(t, func(http.ResponseWriter, *http.Request) { dispatched.Add(1) }, "none", true)
	endpoint := "wss" + strings.TrimPrefix(f.server.URL, "https") + "/socket"
	conn, response, err := websocket.Dial(context.Background(), endpoint, &websocket.DialOptions{
		HTTPClient: f.client,
		Host:       f.envelope.Rule.Host,
		HTTPHeader: http.Header{
			ResourceHeader: {f.envelope.Rule.ID},
			"Origin":       {"https://attacker.example.test"},
		},
	})
	if conn != nil {
		conn.CloseNow()
	}
	if response != nil {
		response.Body.Close()
	}
	if err == nil || response == nil || response.StatusCode != http.StatusForbidden || dispatched.Load() != 0 {
		t.Fatal("foreign websocket origin reached native app")
	}
}

func TestBrowserProxyRequiresLeaseAndVerifiedGatewayTLS(t *testing.T) {
	var dispatched atomic.Int32
	f := browserProxyFixture(t, func(http.ResponseWriter, *http.Request) { dispatched.Add(1) }, "none", false)
	response, err := f.client.Do(f.request(t, http.MethodGet, "/"))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusServiceUnavailable || dispatched.Load() != 0 {
		t.Fatal("inactive connector lease reached native app")
	}
	plain := httptest.NewRecorder()
	f.proxy.ServeHTTP(plain, f.request(t, http.MethodGet, "/"))
	if plain.Code != http.StatusUnauthorized || dispatched.Load() != 0 {
		t.Fatal("missing gateway mTLS reached native app")
	}
}

func TestBrowserNoneModeStripsNativeAuthorization(t *testing.T) {
	f := browserProxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" || r.Header.Get("X-Api-Key") != "" {
			t.Error("browser none mode forwarded native authorization")
		}
		w.WriteHeader(http.StatusNoContent)
	}, "none", true)
	r := f.request(t, http.MethodGet, "/")
	r.Header.Set("Authorization", "Bearer native-token")
	r.Header.Set("X-Api-Key", "native-key")
	response, err := f.client.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNoContent {
		t.Fatal("browser none request denied unexpectedly")
	}
}
