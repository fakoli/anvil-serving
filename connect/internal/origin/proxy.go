// Package origin enforces a connector-local destination and native credential
// binding before proxying an already authenticated gateway request.
package origin

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
)

const ResourceHeader = "X-Anvil-Connect-Resource"

var ErrConfiguration = errors.New("invalid origin proxy configuration")

type SecretSource func(name string) (string, bool)

type Proxy struct {
	lease       *access.Lease
	active      *access.Active
	envelope    config.Envelope
	gatewayPeer string
	secrets     SecretSource
	browser     bool
	slots       chan struct{}
	transport   *http.Transport
	proxy       *httputil.ReverseProxy
}

// NewAPI requires peer authentication on the inner HTTP connection. The outer
// tunnel's identity alone does not authorize access through a loopback listener.
// TLS servers must require and verify client certificates under the deployment
// CA; the handler additionally checks the gateway's declared certificate name.
func NewAPI(envelope config.Envelope, gatewayPeer string, secrets SecretSource, lease *access.Lease) (*Proxy, error) {
	declaration := config.Connector{Schema: "anvil-connect.connector/v1", ID: "local", Resources: []config.Envelope{envelope}}
	if declaration.Validate() != nil || envelope.Rule.Access != "api" || !config.ValidHost(gatewayPeer) || secrets == nil || lease == nil || lease.Binding().Resource != envelope.Rule.ID {
		return nil, ErrConfiguration
	}
	return newProxy(envelope, gatewayPeer, lease, secrets, false)
}

// NewBrowser fixes a browser resource to its declared connector-local origin.
// Browser cookies and native CSRF/application headers remain application
// authority; no delegation secret is loaded or injected on this path.
func NewBrowser(envelope config.Envelope, gatewayPeer string, lease *access.Lease) (*Proxy, error) {
	declaration := config.Connector{Schema: "anvil-connect.connector/v1", ID: "local", Resources: []config.Envelope{envelope}}
	if declaration.Validate() != nil || envelope.Rule.Access != "browser" || (envelope.Rule.NativeAuth != "none" && envelope.Rule.NativeAuth != "passthrough") || envelope.TokenEnv != "" || !config.ValidHost(gatewayPeer) || lease == nil || lease.Binding().Resource != envelope.Rule.ID {
		return nil, ErrConfiguration
	}
	return newProxy(envelope, gatewayPeer, lease, nil, true)
}

func newProxy(envelope config.Envelope, gatewayPeer string, lease *access.Lease, secrets SecretSource, browser bool) (*Proxy, error) {
	envelope.Rule.Methods = append([]string(nil), envelope.Rule.Methods...)
	u, err := url.Parse(envelope.OriginURL)
	if err != nil {
		return nil, ErrConfiguration
	}
	idle := time.Duration(envelope.Rule.Limits.IdleSeconds) * time.Second
	p := &Proxy{envelope: envelope, gatewayPeer: gatewayPeer, secrets: secrets, browser: browser, slots: make(chan struct{}, envelope.Rule.Limits.Concurrent), lease: lease}
	var activeErr error
	p.active, activeErr = access.NewActive(envelope.Rule.Limits.Concurrent, 250*time.Millisecond)
	if activeErr != nil {
		return nil, ErrConfiguration
	}
	p.transport = &http.Transport{
		Proxy: nil, DisableKeepAlives: true, DisableCompression: true,
		MaxConnsPerHost: envelope.Rule.Limits.Concurrent, MaxResponseHeaderBytes: 65536,
		ResponseHeaderTimeout: idle,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			if network != "tcp" || address != u.Host {
				return nil, errors.New("origin destination refused")
			}
			conn, err := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", u.Host)
			if err != nil {
				return nil, err
			}
			return relay.WrapConn(conn, idle), nil
		},
	}
	p.proxy = &httputil.ReverseProxy{
		Transport: p.transport, FlushInterval: -1, BufferPool: relay.NewBufferPool(envelope.Rule.Limits), ErrorLog: log.New(io.Discard, "", 0),
		Rewrite: func(request *httputil.ProxyRequest) {
			request.Out.URL.Scheme = u.Scheme
			request.Out.URL.Host = u.Host
			request.Out.Host = envelope.Rule.Host
			request.Out.GetBody = nil // never make an admitted body replayable
			if browser {
				// Browser requests retain only native app material; Connect cookies,
				// gateway assertions, and proxy identity never reach the dashboard.
				if httpedge.CleanBrowserHeaders(request.Out.Header, envelope.Rule.NativeAuth) != nil {
					request.Out.Header = http.Header{}
				}
				return
			}
			httpedge.CleanAPIHeaders(request.Out.Header)
			request.Out.Header.Set("Authorization", "Bearer "+request.In.Context().Value(nativeTokenKey{}).(string))
		},
		ModifyResponse: func(response *http.Response) error {
			if browser {
				return httpedge.ValidateBrowserResponse(response, envelope.Rule)
			}
			return relay.APIResponse(response, envelope.Rule)
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
	return p, nil
}

func (p *Proxy) Close() { p.active.Close(); p.transport.CloseIdleConnections() }

type nativeTokenKey struct{}

func exactOrigin(r *http.Request, host string) bool {
	origins := r.Header.Values("Origin")
	return len(origins) == 1 && origins[0] == "https://"+host
}

func browserOriginAllowed(r *http.Request, host string) bool {
	origins := r.Header.Values("Origin")
	upgrade := len(r.Header.Values("Upgrade")) != 0
	if upgrade || len(origins) != 0 {
		return exactOrigin(r, host)
	}
	return r.Method == http.MethodGet || r.Method == http.MethodHead || r.Method == http.MethodOptions
}

func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.TLS == nil || !r.TLS.HandshakeComplete || r.TLS.Version < tls.VersionTLS13 || len(r.TLS.VerifiedChains) == 0 || len(r.TLS.PeerCertificates) == 0 || !relay.ExactPeerName(r.TLS.PeerCertificates[0], p.gatewayPeer) {
		http.Error(w, "gateway peer denied", http.StatusUnauthorized)
		return
	}
	resources := r.Header.Values(ResourceHeader)
	if len(resources) != 1 || resources[0] != p.envelope.Rule.ID || httpedge.ValidateHead(r) != nil || !p.envelope.Rule.Allows(r.Host, r.URL.Path, r.Method) {
		http.Error(w, "local resource envelope denied", http.StatusForbidden)
		return
	}
	if p.browser && !browserOriginAllowed(r, p.envelope.Rule.Host) {
		http.Error(w, "browser origin denied", http.StatusForbidden)
		return
	}
	if r.ContentLength > p.envelope.Rule.Limits.RequestBytes {
		http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
		return
	}
	select {
	case p.slots <- struct{}{}:
	default:
		http.Error(w, "origin capacity unavailable", http.StatusTooManyRequests)
		return
	}
	defer func() { <-p.slots }()
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(p.envelope.Rule.Limits.DurationSeconds)*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		return
	}
	ctx, release, err := p.active.Watch(ctx, p.lease.Check)
	if err != nil {
		http.Error(w, "connector control lease unavailable", http.StatusServiceUnavailable)
		return
	}
	defer release()
	clean := r.Clone(ctx)
	if p.browser {
		if httpedge.CleanBrowserHeaders(clean.Header, p.envelope.Rule.NativeAuth) != nil {
			http.Error(w, "browser headers denied", http.StatusBadRequest)
			return
		}
	} else {
		token, ok := p.secrets(p.envelope.TokenEnv)
		if !ok || token == "" || len(token) > 4096 || strings.TrimSpace(token) != token || strings.ContainsAny(token, "\r\n\x00\t ") {
			http.Error(w, "native credential unavailable", http.StatusServiceUnavailable)
			return
		}
		clean = clean.WithContext(context.WithValue(ctx, nativeTokenKey{}, token))
	}
	clean.Body = relay.Body(w, http.MaxBytesReader(w, r.Body, p.envelope.Rule.Limits.RequestBytes), ctx, time.Duration(p.envelope.Rule.Limits.IdleSeconds)*time.Second)
	defer clean.Body.Close()
	// The proxy's RoundTripper never follows redirects. Invalid response targets
	// are refused before headers, protecting SDK carriers that follow redirects.
	p.proxy.ServeHTTP(relay.Writer(w, ctx, time.Duration(p.envelope.Rule.Limits.IdleSeconds)*time.Second), clean)
}
