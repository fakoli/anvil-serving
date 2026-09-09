package lab

import (
	"bufio"
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
)

type apiFixture struct {
	gateway   *httptest.Server
	connector *transport.Process
	key       string
	keys      *access.Keys
}

func awaitManaged(t *testing.T, address string, process *transport.Process) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		owned, err := ownsPIDListener(address, process.PID())
		if err != nil {
			t.Fatal(err)
		}
		if owned {
			return
		}
		select {
		case <-process.Done():
			t.Fatal("managed tunnel exited before ready")
		default:
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("managed tunnel did not own its listener")
}

func fullAPI(t *testing.T, native http.HandlerFunc, mutate ...func(*config.Gateway)) apiFixture {
	t.Helper()
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer synthetic-native-token" {
			http.Error(w, "native auth required", 401)
			return
		}
		if r.Header.Get("X-Api-Key") != "" || r.Header.Get(origin.ResourceHeader) != "" {
			t.Error("Connect credential or routing header reached app")
		}
		native(w, r)
	}))
	t.Cleanup(app.Close)
	f, err := os.Open("../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	g, err := config.ReadGateway(f)
	f.Close()
	if err != nil {
		t.Fatal(err)
	}
	reverse := "127.0.0.1:" + loopbackPort(t)
	g.Resources[0].TunnelAddress = reverse
	for _, change := range mutate {
		change(&g)
	}
	ca := testpki.New(t)
	adapter, err := origin.NewAPI(config.Envelope{Rule: g.Resources[0].Rule, Listen: "127.0.0.1:" + loopbackPort(t), OriginURL: app.URL, TokenEnv: "ANVIL_CONNECT_ROUTER_TOKEN"}, transport.GatewayPeer, func(name string) (string, bool) {
		return "synthetic-native-token", name == "ANVIL_CONNECT_ROUTER_TOKEN"
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(adapter.Close)
	inner := httptest.NewUnstartedServer(adapter)
	inner.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, transport.ConnectorPeer("origin-a"), false)}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
	inner.EnableHTTP2 = true
	inner.StartTLS()
	t.Cleanup(func() { inner.CloseClientConnections(); inner.Close() })
	outer := newAuthority(t)
	dir := t.TempDir()
	caPath := filepath.Join(dir, "ca.pem")
	if err := os.WriteFile(caPath, outer.pem, 0600); err != nil {
		t.Fatal(err)
	}
	serverID := outer.issue(t, dir, "gateway", true)
	clientID := outer.issue(t, dir, "connector-a", false)
	serverAddress := "127.0.0.1:" + loopbackPort(t)
	rules := fmt.Sprintf("restrictions:\n  - name: router\n    match:\n      - !PathPrefix '^connector-a$'\n    allow:\n      - !ReverseTunnel\n        protocol: [Tcp]\n        port: [%s]\n        cidr: [127.0.0.1/32]\n", strings.TrimPrefix(reverse, "127.0.0.1:"))
	rulesPath := filepath.Join(dir, "restrictions.yaml")
	if err := os.WriteFile(rulesPath, []byte(rules), 0600); err != nil {
		t.Fatal(err)
	}
	binary := pinnedBinary(t)
	server, err := transport.StartServer(context.Background(), transport.ServerOptions{Binary: binary, Listen: serverAddress, CertificateFile: serverID.certPath, PrivateKeyFile: serverID.keyPath, ClientCAFile: caPath, RestrictionsFile: rulesPath})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
	})
	awaitManaged(t, serverAddress, server)
	empty := filepath.Join(dir, "empty-roots")
	if err := os.Mkdir(empty, 0700); err != nil {
		t.Fatal(err)
	}
	connector, err := transport.StartClient(context.Background(), transport.ClientOptions{Binary: binary, ServerURL: "wss://" + serverAddress, ReverseAddress: reverse, OriginAddress: inner.Listener.Addr().String(), CertificateFile: clientID.certPath, PrivateKeyFile: clientID.keyPath, TrustFile: caPath, EmptyTrustDirectory: empty})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := connector.Close(); err != nil {
			t.Error(err)
		}
	})
	awaitManaged(t, reverse, server)
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { state.Close() })
	keys, err := access.NewKeys(state, []config.Rule{g.Resources[0].Rule})
	if err != nil {
		t.Fatal(err)
	}
	grants := []access.Grant{{Resource: "router", Methods: []string{"GET", "POST"}}}
	if err := keys.SetPrincipal("sdk", grants, false); err != nil {
		t.Fatal(err)
	}
	key, _, err := keys.Issue("sdk", grants, 0)
	if err != nil {
		t.Fatal(err)
	}
	dispatcher, err := transport.NewDispatcher(g, ca.Roots, ca.Leaf(t, transport.GatewayPeer, true))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(dispatcher.Close)
	edge, err := httpedge.NewGateway(g, keys, dispatcher.Dispatch)
	if err != nil {
		t.Fatal(err)
	}
	gateway := httptest.NewServer(edge)
	t.Cleanup(func() { gateway.CloseClientConnections(); gateway.Close() })
	return apiFixture{gateway, connector, key, keys}
}

func TestAPIStalledUploadReleasesGatewayAndOrigin(t *testing.T) {
	entered, stopped := make(chan struct{}), make(chan struct{})
	f := fullAPI(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "POST" {
			close(entered)
			io.Copy(io.Discard, r.Body)
			close(stopped)
			return
		}
		io.WriteString(w, "capacity available")
	}, func(g *config.Gateway) {
		g.MaxConcurrent = 1
		g.Resources[0].Rule.Limits.Concurrent = 1
		g.Resources[0].Rule.Limits.IdleSeconds = 1
		g.Resources[0].Rule.Limits.DurationSeconds = 1
	})
	conn, err := net.Dial("tcp4", f.gateway.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := fmt.Fprintf(conn, "POST /v1/chat/completions HTTP/1.1\r\nHost: api.example.test\r\nAuthorization: Bearer %s\r\nContent-Length: 1\r\n\r\n", f.key); err != nil {
		t.Fatal(err)
	}
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("native request did not start")
	}
	select {
	case <-stopped:
	case <-time.After(2500 * time.Millisecond):
		t.Fatal("stalled SDK upload survived deadline")
	}
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Do(f.request(t, "GET", "/v1/models"))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil || response.StatusCode != 200 || string(body) != "capacity available" {
		t.Fatal("stalled upload leaked gateway/origin capacity", err, response.StatusCode)
	}
}

func TestAPIHTTP2UnknownLengthBodyThroughTunnel(t *testing.T) {
	bodySeen := make(chan string, 1)
	f := fullAPI(t, func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		bodySeen <- string(body)
		io.WriteString(w, "accepted")
	})
	h2 := httptest.NewUnstartedServer(f.gateway.Config.Handler)
	h2.EnableHTTP2 = true
	h2.StartTLS()
	defer h2.Close()
	r, err := http.NewRequest("POST", h2.URL+"/v1/chat/completions", io.NopCloser(strings.NewReader(`{"stream":true}`)))
	if err != nil {
		t.Fatal(err)
	}
	r.Host = "api.example.test"
	r.Header.Set("Authorization", "Bearer "+f.key)
	response, err := h2.Client().Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.ProtoMajor != 2 || response.StatusCode != 200 {
		t.Fatal("HTTP/2 request failed", response.Proto, response.StatusCode)
	}
	select {
	case body := <-bodySeen:
		if body != `{"stream":true}` {
			t.Fatal("unknown-length body changed")
		}
	case <-time.After(time.Second):
		t.Fatal("body not delivered")
	}
}

func (f apiFixture) request(t *testing.T, method, path string) *http.Request {
	t.Helper()
	r, err := http.NewRequest(method, f.gateway.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	r.Host = "api.example.test"
	r.Header.Set("Authorization", "Bearer "+f.key)
	return r
}

func TestAPIKeyThroughManagedTunnelStreamsAndCancels(t *testing.T) {
	stopped := make(chan struct{})
	var calls atomic.Int32
	f := fullAPI(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		defer close(stopped)
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: admitted\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	client := &http.Client{Timeout: 5 * time.Second}
	r := f.request(t, "POST", "/v1/chat/completions").WithContext(ctx)
	response, err := client.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	line, err := bufio.NewReader(response.Body).ReadString('\n')
	if err != nil || line != "data: admitted\n" {
		t.Fatal("API stream not flushed", err, response.StatusCode)
	}
	cancel()
	select {
	case <-stopped:
	case <-time.After(2 * time.Second):
		t.Fatal("native request survived SDK cancellation")
	}
	if calls.Load() != 1 {
		t.Fatal("unexpected origin dispatch count")
	}
	t.Log("one scoped key -> gateway -> pinned reverse tunnel -> inner mTLS -> native bearer -> first SSE event; cancellation reached origin")
}

func TestAPIConnectorFailureDoesNotReplayPost(t *testing.T) {
	started := make(chan struct{})
	stopped := make(chan struct{})
	var calls atomic.Int32
	f := fullAPI(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		close(started)
		defer close(stopped)
		<-r.Context().Done()
	})
	done := make(chan int, 1)
	go func() {
		response, err := (&http.Client{Timeout: 5 * time.Second}).Do(f.request(t, "POST", "/v1/chat/completions"))
		if err != nil {
			done <- 0
			return
		}
		defer response.Body.Close()
		done <- response.StatusCode
	}()
	select {
	case <-started:
	case <-time.After(3 * time.Second):
		t.Fatal("native request not dispatched")
	}
	if err := f.connector.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case status := <-done:
		if status != 502 {
			t.Fatal("interrupted request was not reported as gateway error", status)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("request survived connector failure")
	}
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("native connection not cancelled")
	}
	if calls.Load() != 1 {
		t.Fatal("ambiguous POST was replayed")
	}
}

type apiRoundTripper func(*http.Request) (*http.Response, error)

func (f apiRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestAPIWebSocketThroughManagedTunnel(t *testing.T) {
	done := make(chan struct{})
	f := fullAPI(t, func(w http.ResponseWriter, r *http.Request) {
		defer close(done)
		conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"anvil.v1"}})
		if err != nil {
			return
		}
		defer conn.CloseNow()
		conn.SetReadLimit(131072)
		ctx, cancel := context.WithTimeout(r.Context(), 4*time.Second)
		defer cancel()
		kind, data, err := conn.Read(ctx)
		if err != nil {
			return
		}
		if conn.Write(ctx, kind, data) != nil {
			return
		}
		conn.Read(ctx)
	})
	client := &http.Client{Transport: apiRoundTripper(func(r *http.Request) (*http.Response, error) {
		r = r.Clone(r.Context())
		r.Host = "api.example.test"
		return http.DefaultTransport.RoundTrip(r)
	})}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, strings.Replace(f.gateway.URL, "http://", "ws://", 1)+"/v1/events", &websocket.DialOptions{HTTPClient: client, HTTPHeader: http.Header{"Authorization": []string{"Bearer " + f.key}}, Subprotocols: []string{"anvil.v1"}})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.CloseNow()
	payload := []byte(strings.Repeat("x", 65536))
	if err := conn.Write(ctx, websocket.MessageBinary, payload); err != nil {
		t.Fatal(err)
	}
	conn.SetReadLimit(131072)
	kind, data, err := conn.Read(ctx)
	if err != nil || kind != websocket.MessageBinary || string(data) != string(payload) || conn.Subprotocol() != "anvil.v1" {
		t.Fatal("WebSocket changed through tunnel", err)
	}
	if err := conn.Close(websocket.StatusNormalClosure, "done"); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("native WebSocket did not close")
	}
}
