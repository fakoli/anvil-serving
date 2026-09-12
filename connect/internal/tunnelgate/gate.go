package tunnelgate

import (
	"bytes"
	"context"
	"crypto"
	"crypto/sha1" // RFC 6455 handshake; not used for identity or credentials.
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"reflect"
	"strings"
	"sync/atomic"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
)

const BackendPeer = "tunnel.anvil-connect.internal"
const GatePeer = "tunnel-gate.anvil-connect.internal"

type Backend struct {
	Address string
	// PathToken matches the gate certificate's Common Name because wstunnel
	// enforces that equality for mTLS. AuthToken is independent per resource.
	PathToken string
	AuthToken string
}

type gateRoute struct {
	resource config.Resource
	backend  Backend
	proxy    *httputil.ReverseProxy
	slots    chan struct{}
}

type Gate struct {
	Events    relay.Events
	host      string
	leases    *Leases
	active    *access.Active
	routes    map[string]gateRoute
	transport *http.Transport
}

// TransportCapacity is the existing derived tunnel budget, not the
// dispatcher's application-request cap. Entry mounts must not multiply it.
func (g *Gate) TransportCapacity() int { return g.transport.MaxConnsPerHost }

func opaque(value string) bool {
	b, err := hex.DecodeString(value)
	return err == nil && len(b) == 32 && hex.EncodeToString(b) == value
}

// New requires a private loopback WSS backend under a dedicated trust root and
// gate-only client identity. Mount the public handler on its owned Unix socket
// behind the declared TLS edge; mount Bind's local handler only on the verified
// dedicated TLS 1.3 listener with its own SNI and HTTP/1.1 admission contract.
func New(host string, declaration config.Gateway, leases *Leases, backends map[string]Backend, roots *x509.CertPool, certificate tls.Certificate) (*Gate, error) {
	if !config.ValidHost(host) || declaration.Validate() != nil || leases == nil || len(backends) != len(declaration.Resources) || roots == nil || !validGateCertificate(certificate, roots) {
		return nil, ErrDenied
	}
	gateLeaf, _ := x509.ParseCertificate(certificate.Certificate[0])
	for _, resource := range declaration.Resources {
		if resource.Rule.Host == host || !reflect.DeepEqual(leases.routes[resource.Rule.ID], resource) {
			return nil, ErrDenied
		}
	}
	g := &Gate{host: host, leases: leases, routes: map[string]gateRoute{}}
	var err error
	maximum := min(512, declaration.MaxConcurrent+2*len(declaration.Resources))
	g.active, err = access.NewActive(maximum, 250*time.Millisecond)
	if err != nil {
		return nil, ErrDenied
	}
	deny := func() (*Gate, error) { g.Close(); return nil, ErrDenied }
	g.transport = &http.Transport{
		Proxy: nil, DisableKeepAlives: true, DisableCompression: true,
		MaxConnsPerHost: maximum, MaxResponseHeaderBytes: 8192,
		TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 40 * time.Second,
		TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots.Clone(), ServerName: BackendPeer, Certificates: []tls.Certificate{certificate}, VerifyConnection: func(state tls.ConnectionState) error {
			if len(state.VerifiedChains) == 0 || len(state.PeerCertificates) == 0 || !relay.ExactPeerName(state.PeerCertificates[0], BackendPeer) {
				return ErrDenied
			}
			return nil
		}},
	}
	g.transport.Protocols = new(http.Protocols)
	g.transport.Protocols.SetHTTP1(true)
	addresses := map[string]bool{}
	for _, backend := range backends {
		addresses[backend.Address] = true
	}
	g.transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || !addresses[address] || !config.LoopbackAddress(address) {
			return nil, ErrDenied
		}
		conn, err := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", address)
		if err != nil {
			return nil, err
		}
		return relay.WrapConn(conn, 45*time.Second), nil
	}
	tokens := map[string]bool{}
	for _, resource := range declaration.Resources {
		backend, ok := backends[resource.Rule.ID]
		if !ok || !config.LoopbackAddress(backend.Address) || !opaque(backend.PathToken) || backend.PathToken != gateLeaf.Subject.CommonName || !opaque(backend.AuthToken) || tokens[backend.AuthToken] {
			return deny()
		}
		tokens[backend.AuthToken] = true
		proxy := &httputil.ReverseProxy{
			Transport: g.transport, ErrorLog: log.New(io.Discard, "", 0),
			Rewrite: func(p *httputil.ProxyRequest) {
				p.Out.URL.Scheme, p.Out.URL.Host = "https", backend.Address
				p.Out.URL.Path = "/" + backend.PathToken + "/events"
				p.Out.Host = BackendPeer
				p.Out.Header = http.Header{
					"Connection": {"Upgrade"}, "Upgrade": {"websocket"},
					"Sec-Websocket-Version":  {"13"},
					"Sec-Websocket-Key":      {p.In.Header.Get("Sec-WebSocket-Key")},
					"Sec-Websocket-Protocol": {p.In.Header.Get("Sec-WebSocket-Protocol")},
					"Authorization":          {"Bearer " + backend.AuthToken},
				}
				p.Out.Body, p.Out.GetBody = nil, nil
			},
			ModifyResponse: func(r *http.Response) error {
				if r.StatusCode != http.StatusSwitchingProtocols {
					return ErrDenied
				}
				upgrade, ok := one(r.Header, "Upgrade")
				connection, connOK := one(r.Header, "Connection")
				protocol, protoOK := one(r.Header, "Sec-WebSocket-Protocol")
				accept, acceptOK := one(r.Header, "Sec-WebSocket-Accept")
				want := sha1.Sum([]byte(r.Request.Header.Get("Sec-WebSocket-Key") + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
				if !ok || !strings.EqualFold(upgrade, "websocket") || !connOK || !strings.EqualFold(connection, "upgrade") || !protoOK || protocol != "v1" || !acceptOK || accept != base64.StdEncoding.EncodeToString(want[:]) || len(r.Header.Values("Set-Cookie")) != 0 {
					return ErrDenied
				}
				r.Header = http.Header{"Connection": {"Upgrade"}, "Upgrade": {"websocket"}, "Sec-Websocket-Protocol": {"v1"}, "Sec-Websocket-Accept": {accept}}
				if attempt, ok := r.Request.Context().Value(attemptKey{}).(*upgradeAttempt); ok {
					if (attempt.timer != nil && !attempt.timer.Stop()) || r.Request.Context().Err() != nil {
						return ErrDenied
					}
					attempt.established.Store(true)
					attempt.onEstablished()
				}
				return nil
			},
			ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) { gateFailure(w, http.StatusBadGateway) },
		}
		resource.Rule.Methods = append([]string(nil), resource.Rule.Methods...)
		g.routes[resource.Rule.ID] = gateRoute{resource, backend, proxy, make(chan struct{}, min(258, resource.Rule.Limits.Concurrent+2))}
	}
	return g, nil
}

func validGateCertificate(certificate tls.Certificate, roots *x509.CertPool) bool {
	if len(certificate.Certificate) == 0 {
		return false
	}
	leaf, err := x509.ParseCertificate(certificate.Certificate[0])
	signer, ok := certificate.PrivateKey.(crypto.Signer)
	if err != nil || !ok || !relay.ExactPeerName(leaf, GatePeer) || !opaque(leaf.Subject.CommonName) {
		return false
	}
	public, err := x509.MarshalPKIXPublicKey(signer.Public())
	if err != nil || !bytes.Equal(public, leaf.RawSubjectPublicKeyInfo) {
		return false
	}
	intermediates := x509.NewCertPool()
	for _, der := range certificate.Certificate[1:] {
		cert, err := x509.ParseCertificate(der)
		if err != nil {
			return false
		}
		intermediates.AddCert(cert)
	}
	_, err = leaf.Verify(x509.VerifyOptions{Roots: roots, Intermediates: intermediates, DNSName: GatePeer, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}})
	return err == nil
}

func (g *Gate) Close() {
	if g.active != nil {
		g.active.Close()
	}
	if g.transport != nil {
		g.transport.CloseIdleConnections()
	}
}

func gateFailure(w http.ResponseWriter, status int) {
	w.Header().Set("Cache-Control", "no-store")
	http.Error(w, http.StatusText(status), status)
}

func (g *Gate) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	g.serveEntry(w, r, g.host, "public")
}

// Bind changes only the entry's expected Host, never the shared gate or limits.
// The caller must supply the dedicated verified TLS mount, not general ingress.
func (g *Gate) Bind(host string) (http.Handler, error) {
	if !config.ValidHost(host) || host == g.host {
		return nil, ErrDenied
	}
	for _, route := range g.routes {
		if host == route.resource.Rule.Host {
			return nil, ErrDenied
		}
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		g.serveEntry(w, r, host, "local")
	}), nil
}

type attemptKey struct{}
type upgradeAttempt struct {
	timer         *time.Timer
	established   atomic.Bool
	onEstablished func()
}

func (g *Gate) serveEntry(w http.ResponseWriter, r *http.Request, host, path string) {
	raw, protocol, valid := upgradeHead(r, host)
	if !valid {
		g.Events.Record(path, "", "tunnel_establishment_failed")
		gateFailure(w, http.StatusBadRequest)
		return
	}
	admission, err := g.leases.Authenticate(raw)
	if err != nil {
		g.Events.Record(path, "", "authority_denied")
		gateFailure(w, http.StatusUnauthorized)
		return
	}
	route, ok := g.routes[admission.Resource]
	if !ok || !descriptor(protocol, route.resource.TunnelAddress) {
		g.Events.Record(path, admission.Resource, "tunnel_establishment_failed")
		gateFailure(w, http.StatusForbidden)
		return
	}
	select {
	case route.slots <- struct{}{}:
	default:
		gateFailure(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-route.slots }()
	ctx, cancel := context.WithTimeout(r.Context(), 24*time.Hour)
	defer cancel()
	// Local admission gets three seconds for the entire backend dial/TLS/101,
	// not three seconds per stage. Stop the timer only after verified upgrade.
	attempt := &upgradeAttempt{onEstablished: func() { g.Events.Record(path, admission.Resource, "tunnel_established") }}
	if path == "local" {
		attempt.timer = time.AfterFunc(3*time.Second, cancel)
		defer attempt.timer.Stop()
	}
	ctx = context.WithValue(ctx, attemptKey{}, attempt)
	ctx, release, err := g.active.Watch(ctx, func() error { return g.leases.Check(admission) })
	if err != nil {
		gateFailure(w, http.StatusUnauthorized)
		return
	}
	defer release()
	// ReverseProxy owns the raw upgraded bytes. Do not reframe them with a
	// WebSocket library: the pinned tunnel may use unmasked client frames.
	route.proxy.ServeHTTP(relay.Writer(w, ctx, 45*time.Second), r.Clone(ctx))
	if attempt.established.Load() {
		g.Events.Record(path, admission.Resource, "tunnel_disconnected")
	} else {
		g.Events.Record(path, admission.Resource, "tunnel_establishment_failed")
	}
}

// Restrictions emits a closed, credential-bearing backend file. The lifecycle
// writes it only to an owned 0600 file, never a tracked manifest or log.
func Restrictions(declaration config.Gateway, backends map[string]Backend) ([]byte, error) {
	if declaration.Validate() != nil || len(backends) != len(declaration.Resources) {
		return nil, ErrDenied
	}
	var result strings.Builder
	result.WriteString("restrictions:\n")
	seenTokens := map[string]bool{}
	path := ""
	for _, resource := range declaration.Resources {
		b, ok := backends[resource.Rule.ID]
		if !ok || !config.LoopbackAddress(b.Address) || !opaque(b.PathToken) || !opaque(b.AuthToken) || (path != "" && b.PathToken != path) || seenTokens[b.AuthToken] {
			return nil, ErrDenied
		}
		path, seenTokens[b.AuthToken] = b.PathToken, true
		_, port, _ := net.SplitHostPort(resource.TunnelAddress)
		fmt.Fprintf(&result, "  - name: %s\n    match:\n      - !PathPrefix '^%s$'\n      - !Authorization '^Bearer %s$'\n    allow:\n      - !ReverseTunnel\n        protocol: [Tcp]\n        port: [%s]\n        cidr: [127.0.0.1/32]\n", resource.Rule.ID, b.PathToken, b.AuthToken, port)
	}
	return []byte(result.String()), nil
}
