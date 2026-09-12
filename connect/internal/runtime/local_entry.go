package runtime

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"log"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
	"github.com/fakoli/anvil-serving/connect/internal/tunnelgate"
)

// A dedicated local root is a single, current self-signed CA, not a bundle
// that can accidentally import public or inner trust alongside it.
func localRoot(path string, excluded ...*x509.CertPool) (*x509.Certificate, *x509.CertPool, error) {
	data, err := readManagedMaterial(path, false)
	if err != nil {
		return nil, nil, ErrConfiguration
	}
	roots, err := parsePublicCA(string(data))
	if err != nil {
		return nil, nil, ErrConfiguration
	}
	block, _ := pem.Decode(data)
	root, err := x509.ParseCertificate(block.Bytes)
	if err != nil || root.CheckSignatureFrom(root) != nil || time.Now().Before(root.NotBefore) || !time.Now().Before(root.NotAfter) {
		return nil, nil, ErrConfiguration
	}
	for _, pool := range excluded {
		if pool == nil {
			continue
		}
		if _, err := root.Verify(x509.VerifyOptions{Roots: pool, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageAny}}); err == nil {
			return nil, nil, ErrConfiguration
		}
	}
	return root, roots, nil
}

func (l LocalTunnelListener) loadTLS(excluded ...*x509.Certificate) (*tls.Config, error) {
	root, roots, err := localRoot(l.TrustFile)
	if err != nil {
		return nil, err
	}
	for _, cert := range excluded {
		if cert == nil || bytes.Equal(root.RawSubjectPublicKeyInfo, cert.RawSubjectPublicKeyInfo) {
			return nil, ErrConfiguration
		}
	}
	certPEM, err := readManagedMaterial(l.CertificateFile, false)
	if err != nil {
		return nil, err
	}
	keyPEM, err := readManagedMaterial(l.PrivateKeyFile, true)
	if err != nil {
		return nil, err
	}
	certificate, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil || len(certificate.Certificate) == 0 {
		return nil, ErrConfiguration
	}
	leaf, err := x509.ParseCertificate(certificate.Certificate[0])
	if err != nil || leaf.IsCA || !relay.ExactPeerName(leaf, l.ServerName) {
		return nil, ErrConfiguration
	}
	intermediates := x509.NewCertPool()
	for _, der := range certificate.Certificate[1:] {
		cert, err := x509.ParseCertificate(der)
		if err != nil {
			return nil, ErrConfiguration
		}
		intermediates.AddCert(cert)
	}
	verify := func() error {
		_, err := leaf.Verify(x509.VerifyOptions{Roots: roots, Intermediates: intermediates, DNSName: l.ServerName, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}})
		if err != nil {
			return ErrConfiguration
		}
		return nil
	}
	if verify() != nil {
		return nil, ErrConfiguration
	}
	return &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13, NextProtos: []string{"http/1.1"}, SessionTicketsDisabled: true,
		GetCertificate: func(hello *tls.ClientHelloInfo) (*tls.Certificate, error) {
			if hello.ServerName != l.ServerName || verify() != nil {
				return nil, ErrConfiguration
			}
			return &certificate, nil
		}}, nil
}

type entryHealth struct {
	mu        sync.Mutex
	listening bool
	reason    string
}

// EntryHealth is listener health, not reverse-registration or origin readiness.
type EntryHealth struct {
	Listening bool
	Reason    string
}

func (e *entryHealth) set(listening bool, reason string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.listening, e.reason = listening, reason
}

func (g *Gateway) EntryHealth(path string) EntryHealth {
	e := g.entries[path]
	if e == nil {
		return EntryHealth{Reason: "undeclared"}
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	return EntryHealth{e.listening, e.reason}
}

func (g *Gateway) entriesUnavailable() bool {
	for path := range g.entries {
		health := g.EntryHealth(path)
		if health.Listening || health.Reason == "" {
			return false
		}
	}
	return true
}

// TLS logs may include peer data; replace their payload with one bounded code.
type entryTLSLog struct{ events *relay.Events }

func (w entryTLSLog) Write(p []byte) (int, error) {
	w.events.Record("local", "", "entry_tls_failed")
	return len(p), nil
}

// Limit accepted local sockets before TLS allocates a handshake goroutine.
// This is an additional entry bound; admission still uses the one shared Gate.
type entryListener struct {
	net.Listener
	slots  chan struct{}
	events *relay.Events
}
type entryConn struct {
	net.Conn
	release func()
}

func (c *entryConn) Close() error { err := c.Conn.Close(); c.release(); return err }
func (l *entryListener) Accept() (net.Conn, error) {
	for {
		conn, err := l.Listener.Accept()
		if err != nil {
			return nil, err
		}
		select {
		case l.slots <- struct{}{}:
			return &entryConn{conn, sync.OnceFunc(func() { <-l.slots })}, nil
		default:
			_ = conn.Close()
			l.events.Record("local", "", "entry_capacity_exhausted")
		}
	}
}

// serveEntry owns a child context: Serve returning must cancel hijacked sessions
// too. http.Server.Close/Shutdown alone do not own hijacked connections.
func (g *Gateway) serveEntry(ctx context.Context, path string, handler http.Handler, listener net.Listener, config *tls.Config) {
	entryCtx, cancel := context.WithCancel(ctx)
	health := g.entries[path]
	server := nativeServer(entryCtx, handler)
	if config != nil {
		server.TLSConfig = config
		server.Protocols = new(http.Protocols)
		server.Protocols.SetHTTP1(true)
		// One second each for TLS and HTTP head, then three for gate admission.
		server.ReadHeaderTimeout = time.Second
		server.IdleTimeout = time.Second
		server.MaxHeaderBytes = 8192
		server.ErrorLog = log.New(entryTLSLog{g.events}, "", 0)
	}
	g.cleanup = append(g.cleanup, func() { cancel(); _ = server.Close(); _ = listener.Close() })
	health.set(true, "entry_listening")
	g.events.Record(path, "", "entry_listening")
	g.wait.Add(1)
	go func() {
		defer g.wait.Done()
		defer cancel()
		if config == nil {
			_ = server.Serve(listener)
		} else {
			_ = server.ServeTLS(listener, "", "")
		}
		_ = server.Close()
		health.set(false, "entry_stopped")
		g.events.Record(path, "", "entry_stopped")
		// Containment protects an available sibling; it must not strand a
		// public-only generation (or a generation with both entries down).
		if ctx.Err() == nil && g.entriesUnavailable() {
			g.cancel()
			select {
			case g.errors <- ErrUnavailable:
			default:
			}
		}
	}()
}

func (g *Gateway) startLocalEntry(ctx context.Context, l LocalTunnelListener, gate *tunnelgate.Gate, excluded ...*x509.Certificate) {
	config, err := l.loadTLS(excluded...)
	if err != nil {
		g.entries["local"].set(false, "entry_tls_failed")
		g.events.Record("local", "", "entry_tls_failed")
		return
	}
	handler, err := gate.Bind(l.HTTPHost)
	if err != nil {
		g.entries["local"].set(false, "entry_bind_failed")
		g.events.Record("local", "", "entry_bind_failed")
		return
	}
	listener, err := net.Listen("tcp4", l.Listen)
	if err != nil {
		g.entries["local"].set(false, "entry_bind_failed")
		g.events.Record("local", "", "entry_bind_failed")
		return
	}
	bounded := &entryListener{listener, make(chan struct{}, gate.TransportCapacity()), g.events}
	g.serveEntry(ctx, "local", handler, bounded, config)
}

// Compare trust by key material, never by pathname or a CA's display name.
func distinctRoot(root *x509.Certificate, pemData []byte) bool {
	count := 0
	for len(bytes.TrimSpace(pemData)) != 0 {
		block, rest := pem.Decode(pemData)
		if block == nil || block.Type != "CERTIFICATE" {
			return false
		}
		cert, err := x509.ParseCertificate(block.Bytes)
		if err != nil || !cert.IsCA || !cert.BasicConstraintsValid || bytes.Equal(root.RawSubjectPublicKeyInfo, cert.RawSubjectPublicKeyInfo) {
			return false
		}
		pemData = rest
		count++
	}
	return count > 0
}

// VerifyLocalTunnelTrust is the material counterpart of ValidateGateway for
// native preflight with both role declarations. It reads only public roots;
// the gateway service separately verifies its private leaf/key through loadTLS.
// Standalone daemons cannot discover the other role's declared trust file.
func (c ConnectorConfig) VerifyLocalTunnelTrust(g GatewayConfig) error {
	if err := c.ValidateGateway(g); err != nil {
		return err
	}
	if c.LocalTunnel == nil {
		return nil
	}
	entry, _, err := localRoot(g.LocalTunnel.TrustFile)
	if err != nil {
		return ErrConfiguration
	}
	connector, _, err := localRoot(c.LocalTunnel.TrustFile)
	if err != nil || !bytes.Equal(entry.Raw, connector.Raw) {
		return ErrConfiguration
	}
	public, err := readManagedMaterial(c.PublicTrustFile, false)
	if err != nil || !distinctRoot(entry, public) {
		return ErrConfiguration
	}
	return nil
}

// VerifyLocalTunnelMaterial uses exactly startup's leaf/key and trust checks.
// No state is initialized or replaced by preflight.
func (g GatewayConfig) VerifyLocalTunnelMaterial() error {
	if g.Validate() != nil {
		return ErrConfiguration
	}
	if g.LocalTunnel == nil {
		return nil
	}
	directory, err := privatefiles.OpenExisting(g.StateDirectory)
	if err != nil {
		return ErrConfiguration
	}
	defer directory.Close()
	inner, backend, err := loadAuthorities(directory)
	if err != nil {
		return err
	}
	_, err = g.LocalTunnel.loadTLS(inner.certificate, backend.certificate)
	return err
}

func (g *Gateway) entryStatus(declaration GatewayConfig, gate *tunnelgate.Gate) []admin.EntryStatus {
	result := []admin.EntryStatus{}
	counts := gate.Registrations()
	for _, path := range []string{"public", "local"} {
		if path == "local" && declaration.LocalTunnel == nil {
			continue
		}
		h := g.EntryHealth(path)
		entry := admin.EntryStatus{Path: path, Listening: h.Listening, Reason: h.Reason, Resources: []admin.ResourceReadiness{}}
		for _, r := range declaration.Gateway.Resources {
			count := counts[path][r.Rule.ID]
			if !h.Listening {
				count = 0
			}
			entry.Resources = append(entry.Resources, admin.ResourceReadiness{Resource: r.Rule.ID, Registrations: count})
		}
		result = append(result, entry)
	}
	return result
}

// WaitLocalTunnelEntry proves the declared dedicated TLS entry, not a bare TCP
// socket. Registration readiness is separately checked through owner admin status.
func (c ConnectorConfig) WaitLocalTunnelEntry(ctx context.Context) error {
	if ctx == nil || ctx.Err() != nil || c.Validate() != nil || c.LocalTunnel == nil {
		return ErrConfiguration
	}
	_, roots, err := localRoot(c.LocalTunnel.TrustFile)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	for {
		attempt, stop := context.WithTimeout(ctx, time.Second)
		err := transport.VerifyLocalEntry(attempt, *c.outerClient().Local, roots)
		stop()
		if err == nil {
			return nil
		}
		select {
		case <-ctx.Done():
			return ErrUnavailable
		case <-time.After(100 * time.Millisecond):
		}
	}
}
