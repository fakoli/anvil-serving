package client

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

type forwardFixture struct {
	forwarder *Forwarder
	server    *httptest.Server
	localKey  string
	remoteKey string
	lookups   atomic.Int32
	dials     atomic.Int32
}

func forwardRule() config.Rule {
	return config.Rule{
		ID:         "router",
		Host:       "api.example.test",
		PathPrefix: "/v1",
		Methods:    []string{"GET", "POST"},
		Access:     "api",
		NativeAuth: "delegate-bearer",
		Limits: config.Limits{
			RequestBytes: 1024, Concurrent: 1, BufferBytes: 4096,
			IdleSeconds: 1, DurationSeconds: 3,
		},
	}
}

func newForwardFixture(t *testing.T, upstream http.Handler) *forwardFixture {
	return newForwardFixtureWithKey(t, upstream, "ac1.remote-connect-key")
}

func newForwardFixtureWithKey(t *testing.T, upstream http.Handler, remoteKey string) *forwardFixture {
	t.Helper()
	ca := testpki.New(t)
	remote := httptest.NewUnstartedServer(upstream)
	remote.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, "api.example.test", false)}}
	remote.EnableHTTP2 = true
	remote.StartTLS()
	t.Cleanup(remote.Close)
	localKey, err := GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	f := &forwardFixture{localKey: localKey, remoteKey: remoteKey}
	dialer := &net.Dialer{Timeout: time.Second}
	dial := func(ctx context.Context, network, address string) (net.Conn, error) {
		f.dials.Add(1)
		if network != "tcp" || address != "api.example.test:443" {
			return nil, errors.New("caller selected an upstream destination")
		}
		return dialer.DialContext(ctx, "tcp", remote.Listener.Addr().String())
	}
	forwarder, err := newForwarder(forwardRule(), "127.0.0.1:18080", localKey, "ANVIL_CONNECT_REMOTE_KEY", func(name string) (string, bool) {
		f.lookups.Add(1)
		return f.remoteKey, name == "ANVIL_CONNECT_REMOTE_KEY"
	}, Options{RootCAs: ca.Roots}, dial)
	if err != nil {
		t.Fatal(err)
	}
	f.forwarder = forwarder
	f.server = httptest.NewServer(forwarder)
	t.Cleanup(func() { f.server.Close(); f.forwarder.Close() })
	return f
}

func (f *forwardFixture) request(t *testing.T, method, path string, body io.Reader) *http.Request {
	t.Helper()
	r, err := http.NewRequest(method, f.server.URL+path, body)
	if err != nil {
		t.Fatal(err)
	}
	r.Host = f.forwarder.Address()
	r.Header.Set("Authorization", "Bearer "+f.localKey)
	return r
}

func TestGenerateKeyCanonicalAndForwardingIsFixed(t *testing.T) {
	key, err := GenerateKey()
	if err != nil || len(key) != len(localKeyPrefix)+43 {
		t.Fatal("local key generation failed", err)
	}
	if _, err := parseLocalKey(key); err != nil {
		t.Fatal("generated key is not canonical", err)
	}
	if _, err := parseLocalKey(key + "="); err == nil {
		t.Fatal("noncanonical local key accepted")
	}

	const remoteKey = "ac1.remote-connect-key"
	var dispatched atomic.Int32
	f := newForwardFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		dispatched.Add(1)
		if r.Host != "api.example.test" || r.URL.RequestURI() != "/v1/chat/completions?model=declared" {
			t.Error("public destination was not fixed")
		}
		if r.Header.Get("Authorization") != "Bearer "+remoteKey {
			t.Error("remote Connect key missing")
		}
		if r.Header.Get("User-Agent") != "anvil-connect/1" {
			t.Error("public request did not identify the actual Connect client")
		}
		for _, header := range []string{"X-Api-Key", "X-Forwarded-Host", "X-Anvil-Connect-Authorization", "X-Anvil-Connect-Authorization-Claim"} {
			if r.Header.Get(header) != "" {
				t.Errorf("caller credential or route header reached upstream: %s", header)
			}
		}
		_, _ = io.WriteString(w, "fixed")
	}))
	r := f.request(t, "POST", "/v1/chat/completions?model=declared", strings.NewReader("payload"))
	r.Header.Set("X-Forwarded-Host", "attacker.example.test")
	r.Header.Set("User-Agent", "Python-urllib/3.13")
	r.Header.Set("X-Anvil-Connect-Authorization", "Bearer caller-remote-key")
	response, err := (&http.Client{Timeout: 2 * time.Second}).Do(r)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(response.Body)
	response.Body.Close()
	if response.StatusCode != http.StatusOK || string(body) != "fixed" || dispatched.Load() != 1 || f.lookups.Load() != 1 || f.dials.Load() != 1 {
		t.Fatal("admitted request was not forwarded exactly once")
	}
}

func TestForwarderRejectsLocalBrowserAndTargetAmbiguityBeforeSecretLookup(t *testing.T) {
	for name, mutate := range map[string]func(*http.Request){
		"wrong-local-host": func(r *http.Request) { r.Host = "127.0.0.1:18081" },
		"origin":           func(r *http.Request) { r.Header.Set("Origin", "https://app.example.test") },
		"cookie":           func(r *http.Request) { r.Header.Set("Cookie", "session=browser") },
		"proxy-auth":       func(r *http.Request) { r.Header.Set("Proxy-Authorization", "Basic abc") },
		"duplicate-key":    func(r *http.Request) { r.Header.Add("Authorization", "Bearer "+r.Header.Get("Authorization")[7:]) },
		"two-carriers":     func(r *http.Request) { r.Header.Set("X-Api-Key", r.Header.Get("Authorization")[7:]) },
		"wrong-key": func(r *http.Request) {
			r.Header.Set("Authorization", "Bearer acl1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
		},
		"connect":     func(r *http.Request) { r.Method = "CONNECT" },
		"trace":       func(r *http.Request) { r.Method = "TRACE" },
		"path-escape": func(r *http.Request) { r.URL.Path = "/v1/../admin" },
		"absolute-target": func(r *http.Request) {
			r.URL.Scheme, r.URL.Host = "https", "attacker.example.test"
		},
	} {
		t.Run(name, func(t *testing.T) {
			var dispatched atomic.Int32
			f := newForwardFixture(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) { dispatched.Add(1) }))
			r := f.request(t, "GET", "/v1/models", nil)
			mutate(r)
			response := httptest.NewRecorder()
			f.forwarder.ServeHTTP(response, r)
			if response.Code < 400 || dispatched.Load() != 0 || f.lookups.Load() != 0 || f.dials.Load() != 0 {
				t.Fatal("denied local request reached secret source or upstream", response.Code)
			}
		})
	}
}

func TestForwarderConfinesRedirects(t *testing.T) {
	var dispatched atomic.Int32
	f := newForwardFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		dispatched.Add(1)
		http.Redirect(w, r, "https://attacker.example.test/v1", http.StatusFound)
	}))
	response, err := (&http.Client{Timeout: 2 * time.Second}).Do(f.request(t, "GET", "/v1/models", nil))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusBadGateway || dispatched.Load() != 1 || f.dials.Load() != 1 {
		t.Fatal("unsafe redirect was followed or exposed")
	}
}

func TestForwarderExplainsLocalAuthenticationFailureBeforeUpstream(t *testing.T) {
	f := newForwardFixture(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("unauthorized request reached upstream")
	}))
	for _, key := range []string{"", "invalid-local-key"} {
		r := f.request(t, "GET", "/v1/models", nil)
		r.URL.Scheme, r.URL.Host = "", "" // incoming origin-form request
		r.Header.Del("Authorization")
		if key != "" {
			r.Header.Set("Authorization", "Bearer "+key)
		}
		response := httptest.NewRecorder()
		f.forwarder.ServeHTTP(response, r)
		body := response.Body.String()
		if response.Code != http.StatusUnauthorized || response.Header().Get("WWW-Authenticate") != `Bearer realm="anvil-connect-local"` || !strings.Contains(body, "missing or invalid local API key") || !strings.Contains(body, "Authorization: Bearer <local-key>") {
			t.Fatal("local authentication failure did not explain the caller remedy")
		}
		if strings.Contains(body, f.localKey) || strings.Contains(body, f.remoteKey) || f.lookups.Load() != 0 || f.dials.Load() != 0 {
			t.Fatal("local denial exposed or used credentials")
		}
	}
}

func TestForwarderAcceptsExactlyOneXAPIKeyCarrier(t *testing.T) {
	var dispatched atomic.Int32
	f := newForwardFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		dispatched.Add(1)
		w.WriteHeader(http.StatusNoContent)
	}))
	r := f.request(t, "GET", "/v1/models", nil)
	r.Header.Del("Authorization")
	r.Header.Set("X-Api-Key", f.localKey)
	response, err := (&http.Client{Timeout: 2 * time.Second}).Do(r)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNoContent || dispatched.Load() != 1 {
		t.Fatal("single X-Api-Key carrier was not admitted")
	}
}

func TestForwarderUsesActualGatewayAPIKeyCarrier(t *testing.T) {
	rule := forwardRule()
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = state.Close() })
	keys, err := access.NewKeys(state, []config.Rule{rule})
	if err != nil {
		t.Fatal(err)
	}
	grants := []access.Grant{{Resource: rule.ID, Methods: []string{"GET"}}}
	if err := keys.SetPrincipal("sdk", grants, false); err != nil {
		t.Fatal(err)
	}
	remoteKey, _, err := keys.Issue("sdk", grants, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	var dispatched atomic.Int32
	declaration := config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:17890", MaxConcurrent: 1, Resources: []config.Resource{{Rule: rule, Connector: "origin-a", TunnelAddress: "127.0.0.1:17891"}}}
	edge, err := httpedge.NewGateway(declaration, keys, func(w http.ResponseWriter, r *http.Request, _ config.Resource, admitted access.Admission) {
		dispatched.Add(1)
		if admitted.Resource != rule.ID || r.Header.Get("Authorization") != "" || r.Header.Get("X-Api-Key") != "" {
			t.Error("gateway did not authenticate and strip the remote API key")
		}
		w.WriteHeader(http.StatusNoContent)
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(edge.Close)
	f := newForwardFixtureWithKey(t, edge, remoteKey)
	response, err := (&http.Client{Timeout: 2 * time.Second}).Do(f.request(t, "GET", "/v1/models", nil))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNoContent || dispatched.Load() != 1 {
		t.Fatal("forwarder remote key did not authenticate at actual API edge", response.StatusCode)
	}
}

func TestForwarderDoesNotRewriteRemoteAuthenticationFailure(t *testing.T) {
	const remoteKey = "ac1.remote-connect-key"
	f := newForwardFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+remoteKey {
			t.Error("forwarder changed provisioned remote credential")
		}
		http.Error(w, "remote key denied", http.StatusUnauthorized)
	}))
	response, err := (&http.Client{Timeout: 2 * time.Second}).Do(f.request(t, "GET", "/v1/models", nil))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusUnauthorized {
		t.Fatal("remote authentication failure was hidden", response.StatusCode)
	}
	body, _ := io.ReadAll(response.Body)
	if string(body) != "remote key denied\n" || response.Header.Get("WWW-Authenticate") == `Bearer realm="anvil-connect-local"` {
		t.Fatal("upstream denial was relabeled as a local authentication failure")
	}
}

func TestForwarderCloseCancelsStreamingRequest(t *testing.T) {
	started, stopped := make(chan struct{}), make(chan struct{})
	f := newForwardFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: ready\n\n")
		w.(http.Flusher).Flush()
		close(started)
		<-r.Context().Done()
		close(stopped)
	}))
	response, err := (&http.Client{Timeout: 2 * time.Second}).Do(f.request(t, "GET", "/v1/events", nil))
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("stream did not reach upstream")
	}
	f.forwarder.Close()
	response.Body.Close()
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("forwarder close did not cancel active stream")
	}
}

func TestForwarderIdleStreamExpiresBeforeDuration(t *testing.T) {
	stopped := make(chan struct{})
	f := newForwardFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ProtoMajor != 2 {
			t.Error("ordinary forwarding did not use HTTP/2")
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: ready\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(stopped)
	}))
	start := time.Now()
	response, err := (&http.Client{Timeout: 4 * time.Second}).Do(f.request(t, "GET", "/v1/events", nil))
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(response.Body)
	response.Body.Close()
	if !strings.Contains(string(body), "data: ready") {
		t.Fatal("fixture did not establish a stream")
	}
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("idle stream retained origin work")
	}
	if elapsed := time.Since(start); elapsed < 750*time.Millisecond || elapsed > 2500*time.Millisecond {
		t.Fatalf("idle stream ended outside 1s idle bound, before 3s duration: %s", elapsed)
	}
}

func TestForwarderPassesWebSocketOverHTTP1Upgrade(t *testing.T) {
	f := newForwardFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			t.Error(err)
			return
		}
		defer conn.CloseNow()
		kind, message, err := conn.Read(r.Context())
		if err == nil {
			err = conn.Write(r.Context(), kind, message)
		}
		if err != nil {
			t.Error(err)
		}
	}))
	url := "ws" + strings.TrimPrefix(f.server.URL, "http") + "/v1/ws"
	conn, response, err := websocket.Dial(context.Background(), url, &websocket.DialOptions{
		Host: f.forwarder.Address(),
		HTTPHeader: http.Header{
			"Authorization": {"Bearer " + f.localKey},
		},
	})
	if err != nil {
		if response != nil {
			response.Body.Close()
		}
		t.Fatal(err)
	}
	defer conn.CloseNow()
	if err := conn.Write(context.Background(), websocket.MessageText, []byte("stream")); err != nil {
		t.Fatal(err)
	}
	_, message, err := conn.Read(context.Background())
	if err != nil || string(message) != "stream" {
		t.Fatal("websocket upgrade did not preserve framed stream", err)
	}
}
