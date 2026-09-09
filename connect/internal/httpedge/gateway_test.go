package httpedge

import (
	"bufio"
	"errors"
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

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func gatewayFixture(t *testing.T, dispatch Dispatch) (*Gateway, string, *access.Keys) {
	t.Helper()
	file, err := os.Open("../../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	declaration, err := config.ReadGateway(file)
	if err != nil {
		t.Fatal(err)
	}
	declaration.MaxConcurrent = 1
	declaration.Resources[0].Rule.Limits.Concurrent = 1
	declaration.Resources[0].Rule.Limits.RequestBytes = 1024
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = state.Close() })
	keys, err := access.NewKeys(state, []config.Rule{declaration.Resources[0].Rule})
	if err != nil {
		t.Fatal(err)
	}
	grants := []access.Grant{{Resource: "router", Methods: []string{"POST", "GET"}}}
	if err := keys.SetPrincipal("sdk", grants, false); err != nil {
		t.Fatal(err)
	}
	raw, _, err := keys.Issue("sdk", grants, 0)
	if err != nil {
		t.Fatal(err)
	}
	gateway, err := NewGateway(declaration, keys, dispatch)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(gateway.Close)
	return gateway, raw, keys
}

func TestGatewayAuthenticatesBeforeDispatch(t *testing.T) {
	var count atomic.Int32
	g, raw, _ := gatewayFixture(t, func(w http.ResponseWriter, r *http.Request, resource config.Resource, admitted access.Admission) {
		count.Add(1)
		if r.Header.Get("Authorization") != "" || r.Header.Get("X-Api-Key") != "" || r.Header.Get("X-Anvil-Connect-Assertion") != "" || r.Header.Get("X-Forwarded-Host") != "" {
			t.Error("untrusted auth reached dispatcher")
		}
		if resource.Rule.ID != "router" || admitted.Principal != "sdk" || admitted.Resource != "router" {
			t.Error("admission missing")
		}
		if r.URL.RequestURI() != "/v1/chat/completions?model=declared" {
			t.Error("request target changed")
		}
		_, _ = io.WriteString(w, "admitted")
	})
	for _, carrier := range []string{"Authorization", "X-Api-Key"} {
		r := request()
		value := raw
		if carrier == "Authorization" {
			value = "Bearer " + raw
		}
		r.Header.Set(carrier, value)
		r.Header.Set("X-Anvil-Connect-Assertion", "forged")
		r.Header.Set("X-Forwarded-Host", "admin.example.test")
		response := httptest.NewRecorder()
		g.ServeHTTP(response, r)
		if response.Code != 200 || response.Body.String() != "admitted" {
			t.Fatal("authorized SDK call denied", response.Code)
		}
		if r.Header.Get(carrier) != value {
			t.Fatal("gateway mutated caller request")
		}
	}
	if count.Load() != 2 {
		t.Fatal("unexpected dispatch count")
	}
	for name, mutate := range map[string]func(*http.Request){
		"missing":        func(r *http.Request) { r.Header.Del("Authorization") },
		"invalid":        func(r *http.Request) { r.Header.Set("Authorization", "Bearer unknown") },
		"duplicate":      func(r *http.Request) { r.Header.Add("Authorization", "Bearer "+raw) },
		"conflict":       func(r *http.Request) { r.Header.Set("X-Api-Key", raw) },
		"wrong-resource": func(r *http.Request) { r.Host = "second.example.test" },
		"wrong-path":     func(r *http.Request) { r.URL.Path = "/admin" },
		"wrong-method":   func(r *http.Request) { r.Method = "DELETE" },
		"ambiguous-path": func(r *http.Request) { r.URL.Path = "/v1/../admin" },
		"oversized":      func(r *http.Request) { r.ContentLength = 1025 },
	} {
		t.Run(name, func(t *testing.T) {
			r := request()
			r.Header.Set("Authorization", "Bearer "+raw)
			mutate(r)
			response := httptest.NewRecorder()
			g.ServeHTTP(response, r)
			if response.Code < 400 || count.Load() != 2 {
				t.Fatal("denied request was dispatched")
			}
			if strings.Contains(response.Body.String(), raw) {
				t.Fatal("credential leaked in error")
			}
		})
	}
}

func TestGatewayCapacityAndCancellation(t *testing.T) {
	started, stopped := make(chan struct{}), make(chan struct{})
	g, raw, _ := gatewayFixture(t, func(w http.ResponseWriter, r *http.Request, _ config.Resource, _ access.Admission) {
		close(started)
		<-r.Context().Done()
		close(stopped)
	})
	server := httptest.NewServer(g)
	defer server.Close()
	client := server.Client()
	client.Timeout = 3 * time.Second
	r, err := http.NewRequest("GET", server.URL+"/v1", nil)
	if err != nil {
		t.Fatal(err)
	}
	r.Host = "api.example.test"
	r.Header.Set("Authorization", "Bearer "+raw)
	done := make(chan struct{})
	go func() {
		defer close(done)
		response, _ := client.Do(r)
		if response != nil {
			response.Body.Close()
		}
	}()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("request not admitted")
	}
	second := httptest.NewRecorder()
	other := request()
	other.Header.Set("Authorization", "Bearer "+raw)
	g.ServeHTTP(second, other)
	if second.Code != 429 {
		t.Fatal("resource admission limit ignored")
	}
	server.CloseClientConnections()
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("client cancellation not propagated")
	}
	<-done
}

func TestUnknownLengthBodyLimit(t *testing.T) {
	g, raw, _ := gatewayFixture(t, func(w http.ResponseWriter, r *http.Request, _ config.Resource, _ access.Admission) {
		body, err := io.ReadAll(r.Body)
		var limit *http.MaxBytesError
		if len(body) != 1024 || !errors.As(err, &limit) {
			t.Error("streaming request body not bounded")
		}
		w.WriteHeader(http.StatusRequestEntityTooLarge)
	})
	r := request()
	r.Header.Set("Authorization", "Bearer "+raw)
	r.ContentLength = -1
	r.ProtoMajor = 2 // unknown-length HTTP/2 body, without HTTP/1 transfer coding
	r.Body = io.NopCloser(strings.NewReader(strings.Repeat("x", 1025)))
	response := httptest.NewRecorder()
	g.ServeHTTP(response, r)
	if response.Code != 413 {
		t.Fatal("body limit not reported")
	}
}

func TestWireParserRefusesDuplicateHostAndFraming(t *testing.T) {
	var dispatched atomic.Int32
	g, raw, _ := gatewayFixture(t, func(http.ResponseWriter, *http.Request, config.Resource, access.Admission) { dispatched.Add(1) })
	server := httptest.NewServer(g)
	defer server.Close()
	for _, head := range []string{
		"GET /v1 HTTP/1.1\r\nHost: api.example.test\r\nHost: admin.example.test\r\n\r\n",
		"POST /v1 HTTP/1.1\r\nHost: api.example.test\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nx",
		"POST /v1 HTTP/1.1\r\nHost: api.example.test\r\nAuthorization: Bearer " + raw + "\r\nTransfer-Encoding: chunked\r\nContent-Length: 6\r\nConnection: close\r\n\r\n1\r\nx\r\n0\r\n\r\n",
	} {
		conn, err := net.DialTimeout("tcp", strings.TrimPrefix(server.URL, "http://"), time.Second)
		if err != nil {
			t.Fatal(err)
		}
		_ = conn.SetDeadline(time.Now().Add(time.Second))
		_, _ = fmt.Fprint(conn, head)
		response, err := http.ReadResponse(bufio.NewReader(conn), nil)
		conn.Close()
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != 400 {
			t.Fatal("ambiguous wire request not refused")
		}
	}
	if dispatched.Load() != 0 {
		t.Fatal("wire ambiguity reached origin")
	}
}
