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

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
)

const ResourceHeader = "X-Anvil-Connect-Resource"

var ErrConfiguration = errors.New("invalid origin proxy configuration")

type SecretSource func(name string) (string, bool)

type Proxy struct {
	envelope    config.Envelope
	gatewayPeer string
	secrets     SecretSource
	slots       chan struct{}
	transport   *http.Transport
	proxy       *httputil.ReverseProxy
}

// NewAPI requires peer authentication on the inner HTTP connection. The outer
// tunnel's identity alone does not authorize access through a loopback listener.
// TLS servers must require and verify client certificates under the deployment
// CA; the handler additionally checks the gateway's declared certificate name.
func NewAPI(envelope config.Envelope, gatewayPeer string, secrets SecretSource) (*Proxy, error) {
	declaration := config.Connector{Schema: "anvil-connect.connector/v1", ID: "local", Resources: []config.Envelope{envelope}}
	if declaration.Validate() != nil || envelope.Rule.Access != "api" || !config.ValidHost(gatewayPeer) || secrets == nil {
		return nil, ErrConfiguration
	}
	envelope.Rule.Methods = append([]string(nil), envelope.Rule.Methods...)
	u, _ := url.Parse(envelope.OriginURL)
	idle := time.Duration(envelope.Rule.Limits.IdleSeconds) * time.Second
	p := &Proxy{envelope: envelope, gatewayPeer: gatewayPeer, secrets: secrets, slots: make(chan struct{}, envelope.Rule.Limits.Concurrent)}
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
			httpedge.CleanAPIHeaders(request.Out.Header)
			request.Out.Header.Set("Authorization", "Bearer "+request.In.Context().Value(nativeTokenKey{}).(string))
		},
		ModifyResponse: func(response *http.Response) error { return relay.APIResponse(response, envelope.Rule) },
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

func (p *Proxy) Close() { p.transport.CloseIdleConnections() }

type nativeTokenKey struct{}

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
	token, ok := p.secrets(p.envelope.TokenEnv)
	if !ok || token == "" || len(token) > 4096 || strings.TrimSpace(token) != token || strings.ContainsAny(token, "\r\n\x00\t ") {
		http.Error(w, "native credential unavailable", http.StatusServiceUnavailable)
		return
	}
	clean := r.Clone(context.WithValue(ctx, nativeTokenKey{}, token))
	clean.Body = relay.Body(w, http.MaxBytesReader(w, r.Body, p.envelope.Rule.Limits.RequestBytes), ctx, time.Duration(p.envelope.Rule.Limits.IdleSeconds)*time.Second)
	defer clean.Body.Close()
	// The proxy's RoundTripper never follows redirects. Invalid response targets
	// are refused before headers, protecting SDK carriers that follow redirects.
	p.proxy.ServeHTTP(relay.Writer(w, ctx, time.Duration(p.envelope.Rule.Limits.IdleSeconds)*time.Second), clean)
}
