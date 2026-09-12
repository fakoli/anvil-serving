package runtime

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
	"github.com/fakoli/anvil-serving/connect/internal/tunnelgate"
)

type entryAuthority struct{}

func (entryAuthority) Check(identity.Installation, string) error { return nil }

func entryMaterial(t *testing.T) (LocalTunnelListener, testpki.Authority) {
	t.Helper()
	ca := testpki.New(t)
	dir := t.TempDir()
	l := LocalTunnelListener{Listen: "127.0.0.1:18443", ServerName: "local-tls.example.test", HTTPHost: "local-http.example.test", CertificateFile: filepath.Join(dir, "leaf.pem"), PrivateKeyFile: filepath.Join(dir, "leaf.key"), TrustFile: filepath.Join(dir, "root.pem")}
	cert, key, err := certificatePEM(ca.Leaf(t, l.ServerName, false))
	if err != nil {
		t.Fatal(err)
	}
	for p, b := range map[string][]byte{l.CertificateFile: cert, l.PrivateKeyFile: key, l.TrustFile: ca.PEM()} {
		if err := os.WriteFile(p, b, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return l, ca
}
func entryGateway(t *testing.T) (*Gateway, context.Context) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	g := &Gateway{cancel: cancel, done: make(chan struct{}), errors: make(chan error, 3), events: &relay.Events{}, entries: map[string]*entryHealth{"public": {}, "local": {}}}
	t.Cleanup(g.Close)
	return g, ctx
}
func localGate(t *testing.T) *tunnelgate.Gate {
	t.Helper()
	d, _ := localTunnelSettings(t)
	ca := testpki.New(t)
	leases, err := tunnelgate.NewLeases(d.Gateway, entryAuthority{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	backends := map[string]tunnelgate.Backend{}
	for n, r := range d.Gateway.Resources {
		backends[r.Rule.ID] = tunnelgate.Backend{Address: d.TunnelListen, PathToken: strings.Repeat("a", 64), AuthToken: fmt.Sprintf("%064x", n+1)}
	}
	gate, err := tunnelgate.New(d.TunnelHost, d.Gateway, leases, backends, ca.Roots, ca.LeafWithCommonName(t, tunnelgate.GatePeer, strings.Repeat("a", 64), true))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(gate.Close)
	return gate
}
func pipeTLS(t *testing.T, l *testpki.PipeListener, cfg *tls.Config) (*tls.Conn, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	c, err := l.DialContext(ctx, "tcp4", l.Addr().String())
	if err != nil {
		return nil, err
	}
	c.SetDeadline(time.Now().Add(2 * time.Second))
	client := tls.Client(c, cfg)
	err = client.HandshakeContext(ctx)
	if err != nil {
		c.Close()
		return nil, err
	}
	return client, nil
}
func TestLocalEntryVerifiedTLSAndAdmissionOnly(t *testing.T) {
	l, ca := entryMaterial(t)
	cfg, err := l.loadTLS()
	if err != nil {
		t.Fatal(err)
	}
	gate := localGate(t)
	h, err := gate.Bind(l.HTTPHost)
	if err != nil {
		t.Fatal(err)
	}
	listener := testpki.NewPipeListener()
	g, ctx := entryGateway(t)
	g.serveEntry(ctx, "local", h, listener, cfg)
	for _, tc := range []struct {
		name    string
		roots   *x509.CertPool
		version uint16
		alpn    []string
		valid   bool
	}{
		{l.ServerName, ca.Roots, tls.VersionTLS13, []string{"http/1.1"}, true},
		{l.ServerName, testpki.New(t).Roots, tls.VersionTLS13, nil, false},
		{l.HTTPHost, ca.Roots, tls.VersionTLS13, nil, false},
		{"127.0.0.1", ca.Roots, tls.VersionTLS13, nil, false},
		{l.ServerName, ca.Roots, tls.VersionTLS12, nil, false},
		{l.ServerName, ca.Roots, tls.VersionTLS13, []string{"h2"}, false},
	} {
		c, err := pipeTLS(t, listener, &tls.Config{RootCAs: tc.roots, ServerName: tc.name, MinVersion: tc.version, MaxVersion: tc.version, NextProtos: tc.alpn})
		if c != nil {
			c.NetConn().Close()
		}
		if (err == nil) != tc.valid {
			t.Fatal("unexpected TLS result", tc.name, tc.version, err)
		}
	}
	for _, path := range []string{"/", "/v1/models", "/control/renew", "/admin", "/oidc/callback", tunnelgate.UpgradePath} {
		c, err := pipeTLS(t, listener, &tls.Config{RootCAs: ca.Roots, ServerName: l.ServerName, MinVersion: tls.VersionTLS13})
		if err != nil {
			t.Fatal(err)
		}
		fmt.Fprintf(c, "GET %s HTTP/1.1\r\nHost: %s\r\n\r\n", path, l.HTTPHost)
		r, err := http.ReadResponse(bufio.NewReader(c), nil)
		if err != nil || r.StatusCode != 400 {
			t.Fatal("general request admitted", path, err)
		}
		io.Copy(io.Discard, r.Body)
		r.Body.Close()
		c.NetConn().Close()
	}
	// Correct upgrade syntax reaches authority only for the bound HTTP Host;
	// the TLS name alone does not authorize this entry.
	for _, host := range []string{l.HTTPHost, l.ServerName, "tunnel.example.test"} {
		c, err := pipeTLS(t, listener, &tls.Config{RootCAs: ca.Roots, ServerName: l.ServerName, MinVersion: tls.VersionTLS13})
		if err != nil {
			t.Fatal(err)
		}
		fmt.Fprintf(c, "GET /acv1/events HTTP/1.1\r\nHost: %s\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: AAAAAAAAAAAAAAAAAAAAAA==\r\nSec-WebSocket-Protocol: v1\r\nAuthorization: Bearer synthetic\r\n\r\n", host)
		r, err := http.ReadResponse(bufio.NewReader(c), nil)
		want := 400
		if host == l.HTTPHost {
			want = 401
		}
		if err != nil || r.StatusCode != want {
			t.Fatal("Host separation", err)
		}
		io.Copy(io.Discard, r.Body)
		r.Body.Close()
		c.NetConn().Close()
	}
	c, err := listener.DialContext(ctx, "tcp4", "")
	if err != nil {
		t.Fatal(err)
	}
	c.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, err = c.Read(make([]byte, 1))
	c.Close()
	if e, ok := err.(net.Error); ok && e.Timeout() {
		t.Fatal("stalled TLS not closed")
	}
	if !g.EntryHealth("local").Listening || len(g.Events()) == 0 {
		t.Fatal("bad peers killed entry or diagnostics missing")
	}
}
func TestLocalEntryMaterialValidation(t *testing.T) {
	for _, mode := range []string{"wrong-root", "key-mismatch", "client-eku", "extra-name", "expired", "future", "shared-key-file", "symlink", "reused-root", "root-private-key"} {
		t.Run(mode, func(t *testing.T) {
			l, ca := entryMaterial(t)
			write := func(p string, b []byte) {
				t.Helper()
				if err := os.WriteFile(p, b, 0600); err != nil {
					t.Fatal(err)
				}
			}
			switch mode {
			case "wrong-root":
				write(l.TrustFile, testpki.New(t).PEM())
			case "key-mismatch":
				_, key, _ := certificatePEM(ca.Leaf(t, l.ServerName, false))
				write(l.PrivateKeyFile, key)
			case "client-eku", "extra-name":
				leaf := ca.Leaf(t, l.ServerName, true)
				if mode == "extra-name" {
					leaf = ca.Leaf(t, l.ServerName, false, l.HTTPHost)
				}
				cert, key, _ := certificatePEM(leaf)
				write(l.CertificateFile, cert)
				write(l.PrivateKeyFile, key)
			case "expired", "future":
				leaf := ca.Leaf(t, l.ServerName, false)
				parsed, _ := x509.ParseCertificate(leaf.Certificate[0])
				root, key := ca.SigningIdentity(t)
				if mode == "expired" {
					parsed.NotAfter = time.Now().Add(-time.Second)
				} else {
					parsed.NotBefore = time.Now().Add(time.Hour)
					parsed.NotAfter = time.Now().Add(2 * time.Hour)
				}
				der, err := x509.CreateCertificate(rand.Reader, parsed, root, parsed.PublicKey, key)
				if err != nil {
					t.Fatal(err)
				}
				_, leafKey, _ := certificatePEM(leaf)
				write(l.PrivateKeyFile, leafKey)
				write(l.CertificateFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
			case "shared-key-file":
				if err := os.Chmod(l.PrivateKeyFile, 0644); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Rename(l.PrivateKeyFile, l.PrivateKeyFile+"-real"); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(l.PrivateKeyFile+"-real", l.PrivateKeyFile); err != nil {
					t.Fatal(err)
				}
			case "reused-root":
				root, _ := ca.SigningIdentity(t)
				if _, err := l.loadTLS(root); err == nil {
					t.Fatal("inner/backend root reused")
				}
				return
			case "root-private-key":
				b, _ := os.ReadFile(l.PrivateKeyFile)
				write(l.TrustFile, append(ca.PEM(), b...))
			}
			if _, err := l.loadTLS(); err == nil {
				t.Fatal("invalid entry material accepted")
			}
		})
	}
}
func TestEntryFailureCancelsOnlyItsUpgradedSessions(t *testing.T) {
	for _, failed := range []string{"local", "public"} {
		t.Run(failed, func(t *testing.T) {
			g, ctx := entryGateway(t)
			listeners := map[string]*testpki.PipeListener{}
			clients := map[string]net.Conn{}
			handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, _, err := http.NewResponseController(relay.Writer(w, r.Context(), time.Second)).Hijack()
				if err != nil {
					return
				}
				defer conn.Close()
				fmt.Fprint(conn, "HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n")
				io.Copy(conn, conn)
			})
			for _, path := range []string{"public", "local"} {
				listener := testpki.NewPipeListener()
				listeners[path] = listener
				g.serveEntry(ctx, path, handler, listener, nil)
				c, err := listener.DialContext(ctx, "tcp4", "")
				if err != nil {
					t.Fatal(err)
				}
				clients[path] = c
				defer c.Close()
				fmt.Fprint(c, "GET /acv1/events HTTP/1.1\r\nHost: entry.example.test\r\n\r\n")
				if _, err := http.ReadResponse(bufio.NewReader(c), nil); err != nil {
					t.Fatal(err)
				}
			}
			listeners[failed].Close()
			clients[failed].SetReadDeadline(time.Now().Add(time.Second))
			_, err := clients[failed].Read(make([]byte, 1))
			if err == nil {
				t.Fatal("failed entry kept upgrade")
			} else if e, ok := err.(net.Error); ok && e.Timeout() {
				t.Fatal("hijack not cancelled")
			}
			sibling := "public"
			if failed == "public" {
				sibling = "local"
			}
			c := clients[sibling]
			c.SetDeadline(time.Now().Add(time.Second))
			c.Write([]byte("x"))
			b := make([]byte, 1)
			if _, err := io.ReadFull(c, b); err != nil || b[0] != 'x' {
				t.Fatal("sibling lost stream", err)
			}
			if g.EntryHealth(failed).Listening || !g.EntryHealth(sibling).Listening {
				t.Fatal("incorrect entry health")
			}
			select {
			case <-g.errors:
				t.Fatal("entry failure reached fatal supervision")
			default:
			}
		})
	}
}
func TestLocalTLSFailureContainedAndSelectionImmutable(t *testing.T) {
	g, ctx := entryGateway(t)
	gate := localGate(t)
	l, _ := entryMaterial(t)
	if err := os.WriteFile(l.CertificateFile, []byte("invalid"), 0600); err != nil {
		t.Fatal(err)
	}
	g.startLocalEntry(ctx, l, gate)
	if g.EntryHealth("local").Listening || g.EntryHealth("local").Reason != "entry_tls_failed" {
		t.Fatal("incorrect failure health")
	}
	select {
	case <-g.errors:
		t.Fatal("TLS failure became shared fatal")
	default:
	}
	_, c := localTunnelSettings(t)
	c.HTTPProxyURL = "http://127.0.0.1:8080"
	public := c
	c.LocalTunnel = nil
	pub := c.outerClient()
	c = public
	local := c.outerClient()
	if local.Local == nil || local.ProxyURL != "" || local.TrustFile != c.LocalTunnel.TrustFile || local.ServerURL != "wss://"+c.LocalTunnel.Address || !local.ViaGate {
		t.Fatal("incorrect local selection")
	}
	if pub.ProxyURL != c.HTTPProxyURL || pub.TrustFile != c.PublicTrustFile || pub.ServerURL != "wss://"+c.TunnelHost {
		t.Fatal("public options changed")
	}
	c.LocalTunnel.ServerName = "changed.example.test"
	if local.Local.ServerName == c.LocalTunnel.ServerName {
		t.Fatal("selection retained mutable declaration")
	}
}

func TestLocalLeaseExpiryEventWithoutRequests(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	lease, err := access.NewLease(access.LeaseBinding{Installation: "origin-a", Resource: "router", Epoch: strings.Repeat("a", 64), Generation: 1}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := lease.RenewFor(lease.Binding(), 1, time.Now(), 100*time.Millisecond); err != nil {
		t.Fatal(err)
	}
	var events relay.Events
	done := make(chan struct{})
	go func() { defer close(done); watchLease(ctx, lease, &events, "local", "router") }()
	deadline := time.NewTimer(time.Second)
	defer deadline.Stop()
	poll := time.NewTicker(10 * time.Millisecond)
	defer poll.Stop()
	for len(events.Snapshot()) == 0 {
		select {
		case <-deadline.C:
			t.Fatal("lease expiry not observed at 250 ms cadence")
		case <-poll.C:
		}
	}
	cancel()
	<-done
	snapshot := events.Snapshot()
	if len(snapshot) != 1 || snapshot[0].Reason != "lease_expired" {
		t.Fatal("wrong expiry evidence", snapshot)
	}
}

func TestLocalNativeBindConflictPreservesForeignListener(t *testing.T) {
	foreign, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Skip("native bind qualification unavailable: sandbox denies sockets")
	}
	defer foreign.Close()
	g, ctx := entryGateway(t)
	gate := localGate(t)
	l, _ := entryMaterial(t)
	l.Listen = foreign.Addr().String()
	g.startLocalEntry(ctx, l, gate)
	if g.EntryHealth("local").Listening || g.EntryHealth("local").Reason != "entry_bind_failed" {
		t.Fatal("bind conflict not contained")
	}
	c, err := net.DialTimeout("tcp4", l.Listen, time.Second)
	if err != nil {
		t.Fatal("foreign listener lost", err)
	}
	c.Close()
	select {
	case <-g.errors:
		t.Fatal("optional bind conflict became shared fatal")
	default:
	}
}

func TestEntryLastListenerFailureIsFatal(t *testing.T) {
	for _, withLocal := range []bool{false, true} {
		t.Run(fmt.Sprint(withLocal), func(t *testing.T) {
			g, ctx := entryGateway(t)
			if !withLocal {
				delete(g.entries, "local")
			} else {
				g.entries["local"].set(false, "entry_tls_failed")
			}
			listener := testpki.NewPipeListener()
			g.serveEntry(ctx, "public", http.NotFoundHandler(), listener, nil)
			listener.Close()
			select {
			case <-g.errors:
			case <-time.After(time.Second):
				t.Fatal("no-entry runtime did not fail")
			}
			if !g.entriesUnavailable() {
				t.Fatal("all-entries-down reported available")
			}
		})
	}
}

func TestLocalCrossRoleTrustUsesCertificatesNotPaths(t *testing.T) {
	g, c := localTunnelSettings(t)
	l, ca := entryMaterial(t)
	g.LocalTunnel = &l
	c.LocalTunnel.TrustFile = filepath.Join(t.TempDir(), "connector-root.pem")
	c.PublicTrustFile = filepath.Join(t.TempDir(), "public-root.pem")
	if err := os.WriteFile(c.LocalTunnel.TrustFile, ca.PEM(), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(c.PublicTrustFile, testpki.New(t).PEM(), 0600); err != nil {
		t.Fatal(err)
	}
	if err := c.VerifyLocalTunnelTrust(g); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(c.LocalTunnel.TrustFile, testpki.New(t).PEM(), 0600); err != nil {
		t.Fatal(err)
	}
	if c.VerifyLocalTunnelTrust(g) == nil {
		t.Fatal("different local roots accepted")
	}
	if err := os.WriteFile(c.LocalTunnel.TrustFile, ca.PEM(), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(c.PublicTrustFile, ca.PEM(), 0600); err != nil {
		t.Fatal(err)
	}
	if c.VerifyLocalTunnelTrust(g) == nil {
		t.Fatal("public trust reused through a different path")
	}
}

func TestLocalEntryCapsSocketsBeforeTLS(t *testing.T) {
	raw := testpki.NewPipeListener()
	defer raw.Close()
	events := &relay.Events{}
	listener := &entryListener{raw, make(chan struct{}, 1), events}
	accepted := make(chan net.Conn)
	go func() {
		for {
			c, err := listener.Accept()
			if err != nil {
				return
			}
			accepted <- c
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	first, err := raw.DialContext(ctx, "tcp4", "")
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	owned := <-accepted
	defer owned.Close()
	second, err := raw.DialContext(ctx, "tcp4", "")
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	second.SetReadDeadline(time.Now().Add(time.Second))
	_, err = second.Read(make([]byte, 1))
	if err == nil {
		t.Fatal("unbounded handshake accepted")
	} else if e, ok := err.(net.Error); ok && e.Timeout() {
		t.Fatal("excess socket not refused")
	}
	owned.Close()
	owned.Close() // release is exactly once, even through relay/TLS cleanup.
	third, err := raw.DialContext(ctx, "tcp4", "")
	if err != nil {
		t.Fatal(err)
	}
	defer third.Close()
	select {
	case next := <-accepted:
		next.Close()
	case <-ctx.Done():
		t.Fatal("slot not released")
	}
}

func TestLocalTrustComparisonRejectsEmptyPublicMaterial(t *testing.T) {
	ca := testpki.New(t)
	root, _ := ca.SigningIdentity(t)
	if distinctRoot(root, nil) || distinctRoot(root, []byte(" \n\t")) {
		t.Fatal("missing public roots accepted as distinct")
	}
}
