package origin

import (
	"bufio"
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

const gatewayName = "gateway.anvil-connect.internal"
const connectorName = "origin-a.connector.anvil-connect.internal"

type fixture struct {
	proxy    *Proxy
	server   *httptest.Server
	client   *http.Client
	ca       testpki.Authority
	envelope config.Envelope
}

func proxyFixture(t *testing.T, handler http.HandlerFunc, source SecretSource, mutate ...func(*config.Envelope)) fixture {
	t.Helper()
	native := httptest.NewServer(handler)
	t.Cleanup(native.Close)
	file, err := os.Open("../../examples/connector.json")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	declaration, err := config.ReadConnector(file)
	if err != nil {
		t.Fatal(err)
	}
	envelope := declaration.Resources[0]
	envelope.OriginURL = native.URL
	for _, change := range mutate {
		change(&envelope)
	}
	if source == nil {
		source = func(name string) (string, bool) { return "synthetic-native-token", name == envelope.TokenEnv }
	}
	p, err := NewAPI(envelope, gatewayName, source)
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
	client := &http.Client{Transport: transport, Timeout: 4 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	return fixture{p, server, client, ca, envelope}
}

func TestStalledUploadReleasesOriginCapacity(t *testing.T) {
	for _, kind := range []string{"idle", "duration"} {
		t.Run(kind, func(t *testing.T) {
			stopped := make(chan struct{})
			entered := make(chan struct{})
			f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
				close(entered)
				io.Copy(io.Discard, r.Body)
				close(stopped)
			}, nil, func(e *config.Envelope) {
				e.Rule.Limits.IdleSeconds = 1
				e.Rule.Limits.DurationSeconds = 2
				e.Rule.Limits.Concurrent = 1
				if kind == "duration" {
					e.Rule.Limits.DurationSeconds = 1
				}
			})
			cfg := f.client.Transport.(*http.Transport).TLSClientConfig.Clone()
			cfg.NextProtos = []string{"http/1.1"}
			conn, err := tls.Dial("tcp4", f.server.Listener.Addr().String(), cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			if _, err := fmt.Fprintf(conn, "POST /v1/chat/completions HTTP/1.1\r\nHost: api.example.test\r\nX-Anvil-Connect-Resource: router\r\nContent-Length: 1\r\n\r\n"); err != nil {
				t.Fatal(err)
			}
			select {
			case <-entered:
			case <-time.After(time.Second):
				t.Fatal("origin never began request")
			}
			select {
			case <-stopped:
			case <-time.After(2500 * time.Millisecond):
				t.Fatal("stalled upload held native request beyond bound")
			}
			deadline := time.Now().Add(500 * time.Millisecond)
			for len(f.proxy.slots) > 0 && time.Now().Before(deadline) {
				time.Sleep(time.Millisecond)
			}
			if len(f.proxy.slots) != 0 {
				t.Fatal("stalled upload leaked origin capacity")
			}
		})
	}
}

func (f fixture) request(t *testing.T, method, path string) *http.Request {
	t.Helper()
	r, err := http.NewRequest(method, f.server.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	r.Host = f.envelope.Rule.Host
	r.Header.Set(ResourceHeader, f.envelope.Rule.ID)
	return r
}

func TestNativeCredentialAndLocalEnvelope(t *testing.T) {
	var dispatched, lookups atomic.Int32
	f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		dispatched.Add(1)
		if r.Header.Get("Authorization") != "Bearer synthetic-native-token" {
			http.Error(w, "native auth required", 401)
			return
		}
		if r.Header.Get("X-Api-Key") != "" || r.Header.Get(ResourceHeader) != "" || r.Header.Get("Tailscale-User-Login") != "" {
			t.Error("caller credentials or identity reached native application")
		}
		if r.Host != "api.example.test" || r.URL.RequestURI() != "/v1/models?mode=declared" {
			t.Error("fixed resource target changed")
		}
		_, _ = io.WriteString(w, "native-authorized")
	}, func(name string) (string, bool) {
		lookups.Add(1)
		return "synthetic-native-token", name == "ANVIL_CONNECT_ROUTER_TOKEN"
	})
	r := f.request(t, "GET", "/v1/models?mode=declared")
	r.Header.Set("Authorization", "Bearer caller-key-must-not-pass")
	r.Header.Set("X-Api-Key", "another-caller-key")
	r.Header.Set("Tailscale-User-Login", "administrator")
	response, err := f.client.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(response.Body)
	response.Body.Close()
	if err != nil || response.StatusCode != 200 || string(body) != "native-authorized" {
		t.Fatal("delegation failed", err)
	}
	for name, mutate := range map[string]func(*http.Request){
		"host":               func(r *http.Request) { r.Host = "admin.example.test" },
		"path":               func(r *http.Request) { r.URL.Path = "/admin" },
		"resource":           func(r *http.Request) { r.Header.Set(ResourceHeader, "dashboard") },
		"duplicate-resource": func(r *http.Request) { r.Header.Add(ResourceHeader, "router") },
		"method":             func(r *http.Request) { r.Method = "DELETE" },
	} {
		t.Run(name, func(t *testing.T) {
			r := f.request(t, "GET", "/v1/models")
			mutate(r)
			response, err := f.client.Do(r)
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			if response.StatusCode != 403 {
				t.Fatal("local envelope widened")
			}
		})
	}
	if dispatched.Load() != 1 || lookups.Load() != 1 {
		t.Fatal("denied request reached credential source or native application")
	}
}

func TestRequiresVerifiedGatewayPeer(t *testing.T) {
	var dispatched atomic.Int32
	f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) { dispatched.Add(1) }, nil)
	plain := httptest.NewRecorder()
	f.proxy.ServeHTTP(plain, f.request(t, "GET", "/v1/models"))
	if plain.Code != 401 {
		t.Fatal("unauthenticated loopback handler request admitted")
	}
	for _, kind := range []string{"missing", "wrong-name", "untrusted", "wildcard", "multi-san"} {
		t.Run(kind, func(t *testing.T) {
			transport := f.client.Transport.(*http.Transport).Clone()
			defer transport.CloseIdleConnections()
			switch kind {
			case "missing":
				transport.TLSClientConfig.Certificates = nil
			case "wrong-name":
				transport.TLSClientConfig.Certificates = []tls.Certificate{f.ca.Leaf(t, "other.anvil-connect.internal", true)}
			case "wildcard":
				transport.TLSClientConfig.Certificates = []tls.Certificate{f.ca.Leaf(t, "*.anvil-connect.internal", true)}
			case "multi-san":
				transport.TLSClientConfig.Certificates = []tls.Certificate{f.ca.Leaf(t, gatewayName, true, "other.anvil-connect.internal")}
			case "untrusted":
				transport.TLSClientConfig.Certificates = []tls.Certificate{testpki.New(t).Leaf(t, gatewayName, true)}
			}
			client := &http.Client{Transport: transport, Timeout: time.Second}
			response, err := client.Do(f.request(t, "GET", "/v1/models"))
			if response != nil {
				response.Body.Close()
			}
			if kind == "wrong-name" || kind == "wildcard" || kind == "multi-san" {
				if err != nil || response.StatusCode != 401 {
					t.Fatal("wrong certificate identity not denied")
				}
			} else if err == nil {
				t.Fatal("unverified peer accepted")
			}
		})
	}
	if dispatched.Load() != 0 {
		t.Fatal("unauthenticated peer reached native application")
	}
}

func TestNativeRejectionSurvives(t *testing.T) {
	f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer actual-native-token" {
			http.Error(w, "native auth required", 401)
			return
		}
		t.Error("incorrect provisioned token bypassed native authentication")
	}, nil)
	response, err := f.client.Do(f.request(t, "GET", "/v1/models"))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 401 {
		t.Fatal("native auth rejection hidden")
	}
}

func TestRedirectCannotEscapeOrDispatchTwice(t *testing.T) {
	for _, location := range []string{"https://other.example.test/v1", "http://127.0.0.1:1/v1", "//other.example.test/v1", "/admin", "/v1/%2e%2e/admin", "/v1/next"} {
		t.Run(location, func(t *testing.T) {
			var count atomic.Int32
			f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
				count.Add(1)
				w.Header().Set("Location", location)
				w.Header().Set("Authorization", "must-not-leave-origin")
				w.Header().Set("Set-Cookie", "native=must-not-leave-origin")
				w.WriteHeader(307)
			}, nil)
			response, err := f.client.Do(f.request(t, "GET", "/v1/models"))
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			want := 502
			if location == "/v1/next" {
				want = 307
			}
			if response.StatusCode != want || count.Load() != 1 {
				t.Fatal("redirect expanded destination or was followed")
			}
			if response.Header.Get("Authorization") != "" || response.Header.Get("Set-Cookie") != "" {
				t.Fatal("origin credential response header leaked")
			}
		})
	}
}

func TestSSEFlushAndCancellation(t *testing.T) {
	stopped := make(chan struct{})
	f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		defer close(stopped)
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := f.request(t, "POST", "/v1/chat/completions").WithContext(ctx)
	response, err := f.client.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	line, err := bufio.NewReader(response.Body).ReadString('\n')
	if err != nil || line != "data: first\n" {
		t.Fatal("SSE buffered or changed", err)
	}
	cancel()
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("cancellation did not reach native HTTP request")
	}
}

func TestInterruptedPostIsNeverReplayed(t *testing.T) {
	var executions atomic.Int32
	f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		executions.Add(1)
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		conn.Close()
	}, nil)
	response, err := f.client.Do(f.request(t, "POST", "/v1/chat/completions"))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 502 || executions.Load() != 1 {
		t.Fatal("ambiguous origin execution was retried")
	}
}

type roundTripper func(*http.Request) (*http.Response, error)

func (f roundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestWebSocketNativeAuthenticationAndClose(t *testing.T) {
	done := make(chan struct{})
	f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		defer close(done)
		if r.Header.Get("Authorization") != "Bearer synthetic-native-token" {
			http.Error(w, "native auth required", 401)
			return
		}
		conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"anvil.v1"}})
		if err != nil {
			return
		}
		defer conn.CloseNow()
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		kind, data, err := conn.Read(ctx)
		if err != nil {
			return
		}
		if err := conn.Write(ctx, kind, data); err != nil {
			return
		}
		_, _, _ = conn.Read(ctx)
	}, nil)
	base := f.client.Transport
	client := &http.Client{Transport: roundTripper(func(r *http.Request) (*http.Response, error) {
		clone := r.Clone(r.Context())
		clone.Host = f.envelope.Rule.Host
		return base.RoundTrip(clone)
	})}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, strings.Replace(f.server.URL, "https://", "wss://", 1)+"/v1/events", &websocket.DialOptions{HTTPClient: client, HTTPHeader: http.Header{ResourceHeader: []string{"router"}}, Subprotocols: []string{"anvil.v1"}})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.CloseNow()
	if err := conn.Write(ctx, websocket.MessageText, []byte("native event")); err != nil {
		t.Fatal(err)
	}
	kind, data, err := conn.Read(ctx)
	if err != nil || kind != websocket.MessageText || string(data) != "native event" || conn.Subprotocol() != "anvil.v1" {
		t.Fatal("WebSocket data or subprotocol changed", err)
	}
	if err := conn.Close(websocket.StatusNormalClosure, "done"); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("WebSocket close did not reach native endpoint")
	}
}
