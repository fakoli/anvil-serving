package tunnelgate

import (
	"bufio"
	"context"
	"crypto/sha1"
	"crypto/tls"
	"encoding/base64"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

func gateFixture(t *testing.T) (*Gate, *authority, string) {
	t.Helper()
	ca := testpki.New(t)
	a := &authority{}
	d := declaration(t)
	d.Resources = d.Resources[:1]
	d.MaxConcurrent = 1
	d.Resources[0].Rule.Limits.Concurrent = 1
	leases, err := NewLeases(d, a, nil)
	if err != nil {
		t.Fatal(err)
	}
	token, _, err := leases.Issue(installed(), "router")
	if err != nil {
		t.Fatal(err)
	}
	backend := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != BackendPeer || r.Header.Get("Authorization") != "Bearer "+strings.Repeat("b", 64) || len(r.TLS.VerifiedChains) == 0 {
			t.Error("backend authority changed")
		}
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		defer conn.Close()
		accept := sha1.Sum([]byte(r.Header.Get("Sec-WebSocket-Key") + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
		fmt.Fprintf(conn, "HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Protocol: v1\r\nSec-WebSocket-Accept: %s\r\n\r\n", base64.StdEncoding.EncodeToString(accept[:]))
		io.Copy(conn, conn)
	})}
	backend.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, BackendPeer, false)}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
	backendListener := testpki.NewPipeListener()
	go backend.ServeTLS(backendListener, "", "")
	t.Cleanup(func() { backend.Close(); backendListener.Close() })
	gate, err := New("tunnel.example.test", d, leases, map[string]Backend{"router": {Address: backendListener.Addr().String(), PathToken: strings.Repeat("a", 64), AuthToken: strings.Repeat("b", 64)}}, ca.Roots, ca.LeafWithCommonName(t, GatePeer, strings.Repeat("a", 64), true))
	if err != nil {
		t.Fatal(err)
	}
	gate.transport.DialContext = backendListener.DialContext
	t.Cleanup(gate.Close)
	return gate, a, token
}

func entryRequest(token, host string) *http.Request {
	r := request()
	r.Host = host
	r.Header.Set("Authorization", "Bearer "+token)
	return r
}

func openUpgrade(t *testing.T, listener *testpki.PipeListener, host, token string) net.Conn {
	t.Helper()
	conn, err := listener.DialContext(context.Background(), "tcp4", listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	conn.SetDeadline(time.Now().Add(4 * time.Second))
	r := entryRequest(token, host)
	if err := r.Write(conn); err != nil {
		t.Fatal(err)
	}
	response, err := http.ReadResponse(bufio.NewReader(conn), r)
	if err != nil || response.StatusCode != 101 {
		conn.Close()
		t.Fatalf("upgrade: %v %v", response, err)
	}
	return conn
}

func TestEntryHostsShareAdmissionAndAuthority(t *testing.T) {
	gate, a, token := gateFixture(t)
	local, err := gate.Bind("local-http.example.test")
	if err != nil {
		t.Fatal(err)
	}
	pub := testpki.NewPipeListener()
	defer pub.Close()
	ps := &http.Server{Handler: gate}
	defer ps.Close()
	go ps.Serve(pub)
	loc := testpki.NewPipeListener()
	defer loc.Close()
	ls := &http.Server{Handler: local}
	defer ls.Close()
	go ls.Serve(loc)
	for _, tc := range []struct {
		h    http.Handler
		host string
	}{{gate, "local-http.example.test"}, {local, "tunnel.example.test"}, {local, "local-tls.example.test"}} {
		w := httptest.NewRecorder()
		tc.h.ServeHTTP(w, entryRequest(token, tc.host))
		if w.Code != 400 {
			t.Fatal("cross-entry Host admitted", w.Code)
		}
	}
	for _, h := range []struct {
		h    http.Handler
		host string
	}{{gate, "tunnel.example.test"}, {local, "local-http.example.test"}} {
		r := entryRequest(token, h.host)
		r.Header.Set("Sec-WebSocket-Protocol", routingJWT(strings.Replace(claims, "9081", "9082", 1)))
		w := httptest.NewRecorder()
		h.h.ServeHTTP(w, r)
		if w.Code != 403 {
			t.Fatal("descriptor escape", w.Code)
		}
	}
	// Capacity is Concurrent+2, shared by both entries, rather than two budgets.
	var conns []net.Conn
	defer func() {
		for _, c := range conns {
			c.Close()
		}
	}()
	for n := 0; n < 3; n++ {
		s, host := pub, "tunnel.example.test"
		if n%2 == 1 {
			s, host = loc, "local-http.example.test"
		}
		conns = append(conns, openUpgrade(t, s, host, token))
	}
	for _, tc := range []struct {
		h    http.Handler
		host string
	}{{gate, "tunnel.example.test"}, {local, "local-http.example.test"}} {
		w := httptest.NewRecorder()
		tc.h.ServeHTTP(w, entryRequest(token, tc.host))
		if w.Code != 429 {
			t.Fatal("shared capacity exceeded", w.Code)
		}
	}
	a.revoked.Store(true)
	for _, c := range conns {
		c.SetReadDeadline(time.Now().Add(time.Second))
		if _, err := c.Read(make([]byte, 1)); err == nil {
			t.Fatal("revoked upgrade survived")
		} else if e, ok := err.(net.Error); ok && e.Timeout() {
			t.Fatal("revocation cancellation timed out")
		}
	}
	for _, tc := range []struct {
		h    http.Handler
		host string
	}{{gate, "tunnel.example.test"}, {local, "local-http.example.test"}} {
		w := httptest.NewRecorder()
		tc.h.ServeHTTP(w, entryRequest(token, tc.host))
		if w.Code != 401 {
			t.Fatal("revoked entry admitted", w.Code)
		}
	}
}

func TestLocalAdmissionDeadlineAndEntryCancellation(t *testing.T) {
	gate, _, token := gateFixture(t)
	local, _ := gate.Bind("local-http.example.test")
	var calls atomic.Int32
	// Stall before upstream TLS. The local total budget must cancel its dial/TLS.
	stalled := testpki.NewPipeListener()
	defer stalled.Close()
	go func() {
		c, e := stalled.Accept()
		if e == nil {
			defer c.Close()
			calls.Add(1)
			io.Copy(io.Discard, c)
		}
	}()
	gate.transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		return stalled.DialContext(ctx, network, address)
	}
	began := time.Now()
	w := httptest.NewRecorder()
	local.ServeHTTP(w, entryRequest(token, "local-http.example.test"))
	if w.Code != 502 || time.Since(began) > 4*time.Second || calls.Load() != 1 {
		t.Fatal("local establishment not bounded", w.Code, time.Since(began))
	}
}

func TestEntryAuthorityExpiryTerminatesBothMounts(t *testing.T) {
	gate, _, token := gateFixture(t)
	began := time.Now()
	var elapsed atomic.Int64
	gate.leases.now = func() time.Time { return began.Add(time.Duration(elapsed.Load())) }
	local, _ := gate.Bind("local-http.example.test")
	var clients []net.Conn
	for _, entry := range []struct {
		h    http.Handler
		host string
	}{{gate, "tunnel.example.test"}, {local, "local-http.example.test"}} {
		listener := testpki.NewPipeListener()
		defer listener.Close()
		server := &http.Server{Handler: entry.h}
		defer server.Close()
		go server.Serve(listener)
		c := openUpgrade(t, listener, entry.host, token)
		defer c.Close()
		clients = append(clients, c)
	}
	elapsed.Store(int64(TransportLeaseLifetime + time.Second))
	for _, c := range clients {
		c.SetReadDeadline(time.Now().Add(time.Second))
		_, err := c.Read(make([]byte, 1))
		if err == nil {
			t.Fatal("expired authority retained stream")
		} else if e, ok := err.(net.Error); ok && e.Timeout() {
			t.Fatal("expiry did not cancel upgrade")
		}
	}
	for _, entry := range []struct {
		h    http.Handler
		host string
	}{{gate, "tunnel.example.test"}, {local, "local-http.example.test"}} {
		w := httptest.NewRecorder()
		entry.h.ServeHTTP(w, entryRequest(token, entry.host))
		if w.Code != 401 {
			t.Fatal("expired authority admitted", w.Code)
		}
	}
}
