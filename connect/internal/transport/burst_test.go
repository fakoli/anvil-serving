package transport

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"errors"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/testidentity"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

// gatedListener delays each tunnel connection setup to reproduce the
// reverse-tunnel handshake latency a cold browser burst pays per inner
// connection, and counts accepts, TLS handshakes, and closes at the envelope.
type gatedListener struct {
	net.Listener
	delay   time.Duration
	accepts atomic.Int32
	closes  atomic.Int32
}

func (l *gatedListener) Accept() (net.Conn, error) {
	conn, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	l.accepts.Add(1)
	if l.delay > 0 {
		time.Sleep(l.delay)
	}
	return &gatedConn{Conn: conn, listener: l}, nil
}

type gatedConn struct {
	net.Conn
	listener *gatedListener
}

func (c *gatedConn) Close() error {
	c.listener.closes.Add(1)
	return c.Conn.Close()
}

// revocableIssuer wraps the fixture issuer so a test can flip installation
// authority while pooled connections are live.
type revocableIssuer struct {
	inner   PeerAuthority
	revoked atomic.Bool
}

func (i *revocableIssuer) VerifyPeer(resource string, leaf *x509.Certificate) (identity.Installation, error) {
	if i.revoked.Load() {
		return identity.Installation{}, errors.New("connector authority revoked")
	}
	return i.inner.VerifyPeer(resource, leaf)
}

type burstEnvelope struct {
	server     *httptest.Server
	gated      *gatedListener
	handshakes atomic.Int32
	requests   atomic.Int32
}

func newBurstEnvelope(t *testing.T, ca testpki.Authority, certificate tls.Certificate, delay time.Duration) *burstEnvelope {
	t.Helper()
	e := &burstEnvelope{}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/", func(w http.ResponseWriter, r *http.Request) {
		e.requests.Add(1)
		if len(r.TLS.VerifiedChains) == 0 {
			t.Error("envelope saw request without verified client identity")
		}
		if r.Header.Get(origin.ResourceHeader) != "router" {
			t.Error("envelope saw request without resource header")
		}
		if r.URL.Path == "/v1/slow" {
			time.Sleep(2 * time.Second)
		}
		_, _ = io.WriteString(w, "envelope ok")
	})
	raw, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	e.gated = &gatedListener{Listener: raw, delay: delay}
	e.server = httptest.NewUnstartedServer(mux)
	e.server.Listener = e.gated
	e.server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{certificate}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
	e.server.TLS.GetConfigForClient = func(*tls.ClientHelloInfo) (*tls.Config, error) {
		e.handshakes.Add(1)
		return nil, nil
	}
	e.server.EnableHTTP2 = true
	e.server.StartTLS()
	t.Cleanup(e.server.Close)
	return e
}

// newBurstStack builds a dispatcher whose single router resource points at a
// burst envelope; wrap optionally decorates the credential issuer under test.
func newBurstStack(t *testing.T, delay time.Duration, wrap func(PeerAuthority) PeerAuthority) (*Dispatcher, config.Resource, *burstEnvelope) {
	t.Helper()
	ca := testpki.New(t)
	g := declaration(t)
	g.MaxConcurrent = 64
	authority := testidentity.New(t, g, ca)
	envelope := newBurstEnvelope(t, ca, authority.Certificates["router"], delay)
	g.Resources[0].TunnelAddress = envelope.server.Listener.Addr().String()
	var peers PeerAuthority = authority.Issuer
	if wrap != nil {
		peers = wrap(peers)
	}
	d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true), peers)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d, g.Resources[0], envelope
}

func burstRequests(t *testing.T, d *Dispatcher, resource config.Resource, n int, path string) []int {
	t.Helper()
	codes := make([]int, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			r := httptest.NewRequest("GET", "http://api.example.test"+path, nil)
			r.RequestURI = path
			r.URL.Scheme, r.URL.Host = "", ""
			w := httptest.NewRecorder()
			d.Dispatch(w, r, resource, access.Admission{Resource: "router", Method: "GET"})
			codes[i] = w.Code
		}(i)
	}
	wg.Wait()
	return codes
}

func anyNot(codes []int, want int) bool {
	for _, code := range codes {
		if code != want {
			return true
		}
	}
	return false
}

func trackedConns(t *testing.T, d *Dispatcher, id string) []*trackedConn {
	t.Helper()
	target, ok := d.resources[id]
	if !ok {
		t.Fatalf("resource %q not bound", id)
	}
	target.conns.mu.Lock()
	defer target.conns.mu.Unlock()
	live := make([]*trackedConn, 0, len(target.conns.conns))
	for c := range target.conns.conns {
		live = append(live, c)
	}
	return live
}

func TestColdBurstMultiplexesOverBoundedConnections(t *testing.T) {
	d, resource, envelope := newBurstStack(t, 300*time.Millisecond, nil)

	start := time.Now()
	codes := burstRequests(t, d, resource, 20, "/v1/models")
	elapsed := time.Since(start)
	if anyNot(codes, 200) {
		t.Fatalf("cold burst returned %v; want 200 for every request", codes)
	}
	if elapsed > 4*time.Second {
		t.Fatalf("cold burst took %s; serialized per-request handshakes would breach the 5s deadline", elapsed)
	}
	if n := envelope.handshakes.Load(); n == 0 || n > 3 {
		t.Fatalf("cold burst used %d inner TLS handshakes; want the bounded budget", n)
	}

	// A warm burst must dial nothing: the pooled h2 connections carry it.
	handshakes, accepts := envelope.handshakes.Load(), envelope.gated.accepts.Load()
	codes = burstRequests(t, d, resource, 20, "/v1/models")
	if anyNot(codes, 200) {
		t.Fatalf("warm burst returned %v", codes)
	}
	if envelope.handshakes.Load() != handshakes || envelope.gated.accepts.Load() != accepts {
		t.Fatal("warm burst dialed a new connection; want pooled reuse")
	}
}

func TestSharedConnectionRetiresOnAuthorityRevocation(t *testing.T) {
	issuer := &revocableIssuer{}
	d, resource, envelope := newBurstStack(t, 0, func(inner PeerAuthority) PeerAuthority { issuer.inner = inner; return issuer })

	if codes := burstRequests(t, d, resource, 4, "/v1/models"); anyNot(codes, 200) {
		t.Fatalf("initial burst returned %v", codes)
	}
	if len(trackedConns(t, d, "router")) == 0 {
		t.Fatal("no pooled connections tracked after warm burst")
	}
	closedBefore := envelope.gated.closes.Load()

	// Revoke: requests against pooled connections must fail promptly at the
	// sweep or handshake recheck, and the offending connections must be
	// retired, not left pooled for future requests.
	issuer.revoked.Store(true)
	codes := burstRequests(t, d, resource, 4, "/v1/models")
	for _, code := range codes {
		if code != 502 && code != 503 {
			t.Fatalf("revoked burst returned %d; want prompt failure, never stale-pool success", code)
		}
	}
	if live := trackedConns(t, d, "router"); len(live) != 0 {
		t.Fatalf("%d connections still tracked after revocation", len(live))
	}
	// Client retirement is synchronous; the server observes EOF and closes
	// its accepted socket on a separate goroutine. Wait only for that witness.
	deadline := time.Now().Add(2 * time.Second)
	for envelope.gated.closes.Load() == closedBefore && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if envelope.gated.closes.Load() == closedBefore {
		t.Fatal("server did not observe a retired pooled connection")
	}

	// Restore authority: a fresh dial must succeed and re-pool.
	issuer.revoked.Store(false)
	if codes := burstRequests(t, d, resource, 4, "/v1/models"); anyNot(codes, 200) {
		t.Fatalf("post-restore burst returned %v", codes)
	}
	if len(trackedConns(t, d, "router")) == 0 {
		t.Fatal("no connection re-pooled after authority restore")
	}
}

func TestStreamCancellationIsolatesSiblings(t *testing.T) {
	d, resource, envelope := newBurstStack(t, 0, nil)

	r := httptest.NewRequest("GET", "http://api.example.test/v1/slow", nil)
	r.RequestURI = "/v1/slow"
	r.URL.Scheme, r.URL.Host = "", ""
	ctx, cancel := context.WithCancel(context.Background())
	r = r.WithContext(ctx)
	w := httptest.NewRecorder()
	done := make(chan int, 1)
	go func() {
		d.Dispatch(w, r, resource, access.Admission{Resource: "router", Method: "GET"})
		done <- w.Code
	}()
	time.Sleep(200 * time.Millisecond)
	cancel()
	select {
	case code := <-done:
		if code == 200 {
			t.Fatal("cancelled slow request completed successfully")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("cancelled slow request did not return")
	}

	// The shared connection must survive: the next requests reuse it.
	handshakes := envelope.handshakes.Load()
	if codes := burstRequests(t, d, resource, 4, "/v1/models"); anyNot(codes, 200) {
		t.Fatalf("post-cancellation burst returned %v", codes)
	}
	if envelope.handshakes.Load() != handshakes {
		t.Fatal("cancellation discarded the shared connection; siblings must stay isolated")
	}
}

func TestUpgradeTransportKeepsFreshConnections(t *testing.T) {
	d, resource, _ := newBurstStack(t, 0, nil)
	target := d.resources[resource.Rule.ID]
	if target.transport.DisableKeepAlives {
		t.Fatal("ordinary transport must reuse pooled h2 connections")
	}
	if target.transport.MaxConnsPerHost != dispatchConnections {
		t.Fatalf("ordinary transport budget is %d; want %d", target.transport.MaxConnsPerHost, dispatchConnections)
	}
	if !target.upgradeTransport.DisableKeepAlives {
		t.Fatal("upgrade transport inherited connection reuse from Clone")
	}
	if target.upgradeTransport.MaxConnsPerHost != target.resource.Rule.Limits.Concurrent {
		t.Fatal("upgrade transport lost its admission-sized connection cap")
	}
}

func TestSlowStreamMultiplexesSiblings(t *testing.T) {
	d, resource, envelope := newBurstStack(t, 0, nil)

	// A slow response stream must not occupy its connection: the h2 conn
	// multiplexes concurrent streams, so a sibling request rides the same
	// pooled connection instead of starving or dialing.
	slowDone := make(chan int, 1)
	r := httptest.NewRequest("GET", "http://api.example.test/v1/slow", nil)
	r.RequestURI = "/v1/slow"
	r.URL.Scheme, r.URL.Host = "", ""
	w := httptest.NewRecorder()
	go func() {
		d.Dispatch(w, r, resource, access.Admission{Resource: "router", Method: "GET"})
		slowDone <- w.Code
	}()
	time.Sleep(300 * time.Millisecond)
	handshakes, accepts := envelope.handshakes.Load(), envelope.gated.accepts.Load()
	codes := burstRequests(t, d, resource, 8, "/v1/models")
	if anyNot(codes, 200) {
		t.Fatalf("sibling burst during slow stream returned %v", codes)
	}
	if envelope.handshakes.Load() != handshakes || envelope.gated.accepts.Load() != accepts {
		t.Fatal("sibling burst dialed a new connection; slow streams must multiplex, not occupy")
	}
	select {
	case code := <-slowDone:
		if code != 200 {
			t.Fatalf("slow stream returned %d", code)
		}
	case <-time.After(4 * time.Second):
		t.Fatal("slow stream did not complete")
	}
}

func TestProactiveCertificateExpiryRetiresPooledConnection(t *testing.T) {
	registry := newConnRegistry()
	raw, other := net.Pipe()
	defer other.Close()
	connected := &trackedConn{Conn: raw, registry: registry}
	registry.add(connected)
	// Synthetic leaf with a 4s lifetime: the 1/10 margin retires at ~3.6s.
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "probe"}, NotBefore: time.Now(), NotAfter: time.Now().Add(4 * time.Second)}
	key := ed25519.NewKeyFromSeed(make([]byte, ed25519.SeedSize))
	der, err := x509.CreateCertificate(rand.Reader, template, template, key.Public(), key)
	if err != nil {
		t.Fatal(err)
	}
	leaf, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	registry.bind(connected, leaf)

	eof := make(chan error, 1)
	go func() { _, err := other.Read(make([]byte, 1)); eof <- err }()
	select {
	case err := <-eof:
		if err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrClosedPipe) {
			t.Fatalf("pipe read returned %v; want EOF after retirement", err)
		}
	case <-time.After(6 * time.Second):
		t.Fatal("expiring connection was not retired before its certificate deadline")
	}
	registry.mu.Lock()
	remaining := len(registry.conns)
	registry.mu.Unlock()
	if remaining != 0 {
		t.Fatalf("%d connections still tracked after expiry retirement", remaining)
	}
}
