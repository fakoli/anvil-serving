package transport

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
	"github.com/fakoli/anvil-serving/connect/internal/testidentity"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

func TestConnectorAuthorityRevocationClosesLiveDispatch(t *testing.T) {
	for _, protocol := range []string{"sse", "websocket"} {
		t.Run(protocol, func(t *testing.T) {
			g := declaration(t)
			ca := testpki.New(t)
			authority := testidentity.New(t, g, ca)
			started, stopped := make(chan struct{}), make(chan struct{})
			inner := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if protocol == "websocket" {
					connection, err := websocket.Accept(w, r, nil)
					if err != nil {
						t.Error(err)
						return
					}
					defer connection.CloseNow()
					close(started)
					_, _, _ = connection.Read(r.Context())
				} else {
					w.Header().Set("Content-Type", "text/event-stream")
					_, _ = io.WriteString(w, "data: ready\n\n")
					w.(http.Flusher).Flush()
					close(started)
					<-r.Context().Done()
				}
				close(stopped)
			}))
			inner.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{authority.Certificates["router"]}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
			inner.EnableHTTP2 = true
			inner.StartTLS()
			defer inner.Close()
			g.Resources[0].TunnelAddress = inner.Listener.Addr().String()
			d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true), authority.Issuer)
			if err != nil {
				t.Fatal(err)
			}
			defer d.Close()
			edge := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				r.Host = "api.example.test"
				d.Dispatch(w, r, g.Resources[0], access.Admission{Resource: "router", Method: "GET"})
			}))
			defer edge.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
			defer cancel()
			if protocol == "websocket" {
				conn, _, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(edge.URL, "http")+"/v1/events", nil)
				if err != nil {
					t.Fatal(err)
				}
				defer conn.CloseNow()
			} else {
				r, _ := http.NewRequestWithContext(ctx, "GET", edge.URL+"/v1/events", nil)
				response, err := http.DefaultClient.Do(r)
				if err != nil {
					t.Fatal(err)
				}
				defer response.Body.Close()
				if response.StatusCode != 200 {
					t.Fatal("stream not admitted", response.StatusCode)
				}
			}
			select {
			case <-started:
			case <-ctx.Done():
				t.Fatal("stream did not reach verified connector")
			}
			start := time.Now()
			if err := authority.Identity.Revoke("origin-a"); err != nil {
				t.Fatal(err)
			}
			select {
			case <-stopped:
				t.Logf("connector revocation cancelled %s in %s", protocol, time.Since(start))
			case <-time.After(time.Second):
				t.Fatal("revoked connector retained active stream")
			}
		})
	}
}

func TestTrustedNameWithoutInstallationBindingDenied(t *testing.T) {
	g := declaration(t)
	ca := testpki.New(t)
	authority := testidentity.New(t, g, ca)
	var calls atomic.Int32
	inner := httptest.NewUnstartedServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }))
	// The same trusted CA and exact SAN are insufficient without the issuer's
	// signed generation/SPKI binding to current installation authority.
	inner.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, ConnectorPeer("origin-a"), false)}}
	inner.EnableHTTP2 = true
	inner.StartTLS()
	defer inner.Close()
	g.Resources[0].TunnelAddress = inner.Listener.Addr().String()
	d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true), authority.Issuer)
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	r := httptest.NewRequest("GET", "/v1/models", nil)
	r.Host = "api.example.test"
	w := httptest.NewRecorder()
	d.Dispatch(w, r, g.Resources[0], access.Admission{Resource: "router", Method: "GET"})
	if w.Code != 502 || calls.Load() != 0 {
		t.Fatal("unbound trusted certificate reached origin", w.Code)
	}
}
