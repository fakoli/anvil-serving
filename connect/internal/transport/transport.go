// Package transport carries admitted requests over a fixed, authenticated inner
// TLS connection. The outer reverse tunnel may not grant application access.
package transport

import (
	"bytes"
	"context"
	"crypto"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/http/httputil"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/browseridentity"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

const GatewayPeer = "gateway.anvil-connect.internal"

// dispatchConnections bounds the inner TLS connection budget of the ordinary
// multiplexed transport. Admission and resource limits already bound concurrent
// application requests; this budget only prevents a cold burst from dialing a
// serialized inner TLS connection per request through the reverse tunnel.
const dispatchConnections = 2

// maxCertificateMargin caps the proactive certificate-expiry retirement lead
// so a long-lived connector leaf is not retired absurdly early.
const maxCertificateMargin = 10 * time.Minute

func ConnectorPeer(id string) string { return id + ".connector.anvil-connect.internal" }

type binding struct {
	resource         config.Resource
	transport        *http.Transport
	upgradeTransport *http.Transport
	proxy            *httputil.ReverseProxy
	upgradeProxy     *httputil.ReverseProxy
	conns            *connRegistry
}

// trackedConn lets the dispatcher retire exactly the pooled inner connection
// whose connector certificate lost authority, instead of failing every future
// request against a stale pool entry.
type trackedConn struct {
	net.Conn
	registry *connRegistry
}

func (c *trackedConn) Close() error {
	c.registry.remove(c)
	return c.Conn.Close()
}

// connRegistry tracks live inner connections by connector leaf DER so that one
// revoked or expiring certificate retires only the connections carrying it.
type connRegistry struct {
	mu     sync.Mutex
	conns  map[*trackedConn][]byte
	byLeaf map[string]map[*trackedConn]struct{}
	timers map[*trackedConn]*time.Timer
}

func newConnRegistry() *connRegistry {
	return &connRegistry{conns: map[*trackedConn][]byte{}, byLeaf: map[string]map[*trackedConn]struct{}{}, timers: map[*trackedConn]*time.Timer{}}
}

func (r *connRegistry) add(c *trackedConn) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.conns[c] = nil
}

// bind records the connection's verified leaf and schedules retirement before
// that leaf expires. Never infer a new generation from a reused key. Reused
// connections bind once: the leaf does not change while the pool entry lives.
func (r *connRegistry) bind(c *trackedConn, leaf *x509.Certificate) {
	key := string(leaf.Raw)
	lifetime := leaf.NotAfter.Sub(leaf.NotBefore)
	margin := lifetime / 10
	if margin > maxCertificateMargin {
		margin = maxCertificateMargin
	}
	delay := time.Until(leaf.NotAfter) - margin
	r.mu.Lock()
	defer r.mu.Unlock()
	if bound, seen := r.conns[c]; !seen {
		return
	} else if string(bound) == key {
		if _, scheduled := r.timers[c]; scheduled {
			return
		}
	} else {
		set := r.byLeaf[string(bound)]
		delete(set, c)
		if len(set) == 0 {
			delete(r.byLeaf, string(bound))
		}
	}
	r.conns[c] = leaf.Raw
	set, ok := r.byLeaf[key]
	if !ok {
		set = map[*trackedConn]struct{}{}
		r.byLeaf[key] = set
	}
	set[c] = struct{}{}
	if delay <= 0 {
		go r.retire(c)
		return
	}
	timer := time.AfterFunc(delay, func() { r.retire(c) })
	r.timers[c] = timer
}

// retire closes one connection and drops it from every index. Closing the raw
// connection makes the transport discard the pool entry and dial fresh.
func (r *connRegistry) retire(c *trackedConn) {
	r.mu.Lock()
	_, live := r.conns[c]
	delete(r.conns, c)
	for key, set := range r.byLeaf {
		delete(set, c)
		if len(set) == 0 {
			delete(r.byLeaf, key)
		}
	}
	if timer, ok := r.timers[c]; ok {
		timer.Stop()
		delete(r.timers, c)
	}
	r.mu.Unlock()
	if live {
		_ = c.Conn.Close()
	}
}

// retireLeaf closes every connection verified against one leaf DER.
func (r *connRegistry) retireLeaf(der []byte) {
	r.mu.Lock()
	victims := make([]*trackedConn, 0, len(r.byLeaf[string(der)]))
	for c := range r.byLeaf[string(der)] {
		victims = append(victims, c)
	}
	r.mu.Unlock()
	for _, c := range victims {
		r.retire(c)
	}
}

func (r *connRegistry) remove(c *trackedConn) { r.retire(c) }

func (r *connRegistry) close() {
	r.mu.Lock()
	victims := make([]*trackedConn, 0, len(r.conns))
	for c := range r.conns {
		victims = append(victims, c)
	}
	r.mu.Unlock()
	for _, c := range victims {
		r.retire(c)
	}
}

// PeerAuthority binds signed TLS material to a currently approved installation.
// The credential issuer implements this using its persisted generation bindings.
type PeerAuthority interface {
	VerifyPeer(string, *x509.Certificate) (identity.Installation, error)
}

type Dispatcher struct {
	resources map[string]binding
	peers     PeerAuthority
	active    *access.Active
}

// NewDispatcher owns TLS configuration: there is no skip-verification or caller
// supplied dial/proxy option. Certificates must be deployment-issued credentials;
// the issuer, not a CSR, assigns the gateway/connector names and EKUs.
func NewDispatcher(declaration config.Gateway, roots *x509.CertPool, certificate tls.Certificate, peers PeerAuthority) (*Dispatcher, error) {
	if declaration.Validate() != nil || roots == nil || len(certificate.Certificate) == 0 || certificate.PrivateKey == nil || peers == nil {
		return nil, errors.New("invalid transport configuration")
	}
	leaf, err := x509.ParseCertificate(certificate.Certificate[0])
	if err != nil || !relay.ExactPeerName(leaf, GatewayPeer) {
		return nil, errors.New("invalid gateway certificate identity")
	}
	signer, ok := certificate.PrivateKey.(crypto.Signer)
	if !ok {
		return nil, errors.New("unsupported gateway private key")
	}
	public, err := x509.MarshalPKIXPublicKey(signer.Public())
	if err != nil {
		return nil, errors.New("invalid gateway private key")
	}
	expected, err := x509.MarshalPKIXPublicKey(leaf.PublicKey)
	if err != nil || !bytes.Equal(public, expected) {
		return nil, errors.New("gateway certificate and key mismatch")
	}
	intermediates := x509.NewCertPool()
	for _, der := range certificate.Certificate[1:] {
		cert, err := x509.ParseCertificate(der)
		if err != nil {
			return nil, errors.New("invalid certificate chain")
		}
		intermediates.AddCert(cert)
	}
	if _, err := leaf.Verify(x509.VerifyOptions{Roots: roots, Intermediates: intermediates, DNSName: GatewayPeer, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}); err != nil {
		return nil, errors.New("invalid gateway certificate trust")
	}
	active, err := access.NewActive(declaration.MaxConcurrent, 250*time.Millisecond)
	if err != nil {
		return nil, errors.New("invalid transport capacity")
	}
	d := &Dispatcher{resources: map[string]binding{}, peers: peers, active: active}
	for _, resource := range declaration.Resources {
		resource.Rule.Methods = append([]string(nil), resource.Rule.Methods...)
		idle := time.Duration(resource.Rule.Limits.IdleSeconds) * time.Second
		conns := newConnRegistry()
		tr := &http.Transport{
			Proxy: nil, DisableCompression: true,
			// Ordinary requests multiplex over a bounded connection budget. A
			// fresh inner TLS connection per request would serialize every
			// cold-burst handshake through the reverse tunnel loop and breach
			// the handshake deadline for the queued tail.
			MaxConnsPerHost: dispatchConnections, MaxResponseHeaderBytes: 65536,
			ResponseHeaderTimeout: idle, TLSHandshakeTimeout: 5 * time.Second,
			TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots.Clone(), Certificates: []tls.Certificate{certificate}, ServerName: ConnectorPeer(resource.Connector)},
			DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
				if network != "tcp" || address != resource.TunnelAddress {
					return nil, errors.New("tunnel destination refused")
				}
				conn, err := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", resource.TunnelAddress)
				if err != nil {
					return nil, err
				}
				tracked := &trackedConn{Conn: relay.WrapConn(conn, idle), registry: conns}
				conns.add(tracked)
				return tracked, nil
			},
		}
		// Ordinary requests require h2 so an admitted unknown-length HTTP/2 body
		// cannot silently fall back to ambiguous HTTP/1 chunked framing. Classic
		// WebSocket upgrades use a separate HTTP/1-only transport.
		tr.Protocols = new(http.Protocols)
		tr.TLSClientConfig.VerifyConnection = func(state tls.ConnectionState) error {
			if len(state.VerifiedChains) == 0 || len(state.PeerCertificates) == 0 || !relay.ExactPeerName(state.PeerCertificates[0], ConnectorPeer(resource.Connector)) {
				return errors.New("connector certificate identity denied")
			}
			_, err := peers.VerifyPeer(resource.Rule.ID, state.PeerCertificates[0])
			return err
		}
		tr.Protocols.SetHTTP2(true)
		upgradeTransport := tr.Clone()
		upgradeTransport.Protocols = new(http.Protocols)
		upgradeTransport.Protocols.SetHTTP1(true)
		// Clone copies the multiplexed budget; upgrades keep their own admission
		// sizing and must never reuse a connection across negotiated streams.
		upgradeTransport.DisableKeepAlives = true
		upgradeTransport.MaxConnsPerHost = resource.Rule.Limits.Concurrent
		proxy := &httputil.ReverseProxy{
			Transport: tr, FlushInterval: -1, BufferPool: relay.NewBufferPool(resource.Rule.Limits), ErrorLog: log.New(io.Discard, "", 0),
			Rewrite: func(request *httputil.ProxyRequest) {
				request.Out.URL.Scheme, request.Out.URL.Host = "https", resource.TunnelAddress
				request.Out.Host = resource.Rule.Host
				request.Out.GetBody = nil
				if resource.Rule.Access == "api" {
					httpedge.CleanAPIHeaders(request.Out.Header)
				} else {
					// BrowserDispatch validates before the transport is invoked.
					_ = httpedge.CleanBrowserHeaders(request.Out.Header, resource.Rule.NativeAuth)
					if resource.Rule.NativeAuth == "signed-identity" {
						assertion, _ := browseridentity.Assertion(request.In.Context())
						request.Out.Header.Set(browseridentity.Header, assertion)
					}
				}
				request.Out.Header.Set(origin.ResourceHeader, resource.Rule.ID)
			},
			ModifyResponse: func(response *http.Response) error {
				if resource.Rule.Access == "browser" {
					return httpedge.ValidateBrowserResponse(response, resource.Rule)
				}
				return relay.APIResponse(response, resource.Rule)
			},
			ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
				var bodyLimit *http.MaxBytesError
				status := http.StatusBadGateway
				if errors.As(err, &bodyLimit) {
					status = http.StatusRequestEntityTooLarge
				}
				http.Error(w, http.StatusText(status), status)
			},
		}
		upgradeProxy := *proxy
		upgradeProxy.Transport = upgradeTransport
		d.resources[resource.Rule.ID] = binding{resource: resource, transport: tr, upgradeTransport: upgradeTransport, proxy: proxy, upgradeProxy: &upgradeProxy, conns: conns}
	}
	return d, nil
}

func (d *Dispatcher) Close() {
	d.active.Close()
	for _, target := range d.resources {
		target.transport.CloseIdleConnections()
		target.upgradeTransport.CloseIdleConnections()
		target.conns.close()
	}
}

// Dispatch is called only by the authenticated edge. It rechecks the immutable
// resource binding and holds the caller's context through the entire response.
func (d *Dispatcher) Dispatch(w http.ResponseWriter, r *http.Request, resource config.Resource, admitted access.Admission) {
	if resource.Rule.Access != "api" || admitted.Resource != resource.Rule.ID || admitted.Method != r.Method {
		http.Error(w, "transport resource denied", http.StatusForbidden)
		return
	}
	d.dispatch(w, r, resource)
}

// BrowserDispatch preserves native application controls after the browser edge
// has authenticated its own session. API admissions cannot select this path.
func (d *Dispatcher) BrowserDispatch(w http.ResponseWriter, r *http.Request, resource config.Resource, admitted session.Admission) {
	if resource.Rule.Access != "browser" || admitted.Resource != resource.Rule.ID || admitted.Host != resource.Rule.Host {
		http.Error(w, "transport resource denied", http.StatusForbidden)
		return
	}
	clean := r.Clone(r.Context())
	if httpedge.CleanBrowserHeaders(clean.Header, resource.Rule.NativeAuth) != nil {
		http.Error(w, "transport headers denied", http.StatusBadRequest)
		return
	}
	if resource.Rule.NativeAuth == "signed-identity" {
		if _, ok := browseridentity.Assertion(clean.Context()); !ok {
			http.Error(w, "transport identity denied", http.StatusForbidden)
			return
		}
	}
	d.dispatch(w, clean, resource)
}

func (d *Dispatcher) dispatch(w http.ResponseWriter, r *http.Request, resource config.Resource) {
	target, ok := d.resources[resource.Rule.ID]
	if !ok || !reflect.DeepEqual(resource, target.resource) || !resource.Rule.Allows(r.Host, r.URL.Path, r.Method) || httpedge.ValidateHead(r) != nil {
		http.Error(w, "transport resource denied", http.StatusForbidden)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(resource.Rule.Limits.DurationSeconds)*time.Second)
	defer cancel()
	// Bound pool wait + dial + inner TLS together, before origin dispatch.
	establishment := time.AfterFunc(5*time.Second, cancel)
	defer establishment.Stop()
	var certificate atomic.Pointer[x509.Certificate]
	ctx, release, err := d.active.Watch(ctx, func() error {
		// Before GotConn the transport has bounded dial/handshake deadlines and
		// its TLS verifier independently checks current installation authority.
		leaf := certificate.Load()
		if leaf == nil {
			return nil
		}
		_, err := d.peers.VerifyPeer(resource.Rule.ID, leaf)
		if err != nil {
			// The pooled connection this request was about to reuse has lost
			// installation authority. Retire every connection carrying that
			// leaf so the pool cannot offer it to future requests.
			target.conns.retireLeaf(leaf.Raw)
		}
		return err
	})
	if err != nil {
		http.Error(w, "transport unavailable", http.StatusServiceUnavailable)
		return
	}
	defer release()
	trace := &httptrace.ClientTrace{GotConn: func(info httptrace.GotConnInfo) {
		connection, ok := info.Conn.(*tls.Conn)
		if !ok {
			cancel()
			return
		}
		state := connection.ConnectionState()
		if len(state.VerifiedChains) == 0 || len(state.PeerCertificates) == 0 {
			cancel()
			return
		}
		// Retain owned signed DER for rechecks through the whole response and
		// upgrade lifetime. Never infer a new generation from a reused key.
		leaf, err := x509.ParseCertificate(append([]byte(nil), state.PeerCertificates[0].Raw...))
		if err != nil {
			cancel()
			return
		}
		certificate.Store(leaf)
		tracked, ok := connection.NetConn().(*trackedConn)
		if !ok {
			cancel()
			return
		}
		if _, err := d.peers.VerifyPeer(resource.Rule.ID, leaf); err != nil {
			// Retire the offending pooled connection before cancelling so the
			// pool cannot re-offer it to the next request.
			target.conns.retire(tracked)
			cancel()
			return
		}
		target.conns.bind(tracked, leaf)
		if !establishment.Stop() {
			cancel()
		}
	}}
	r = r.Clone(httptrace.WithClientTrace(ctx, trace))
	proxy := target.proxy
	if strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
		proxy = target.upgradeProxy
	}
	proxy.ServeHTTP(relay.Writer(w, ctx, time.Duration(resource.Rule.Limits.IdleSeconds)*time.Second), r)
}
