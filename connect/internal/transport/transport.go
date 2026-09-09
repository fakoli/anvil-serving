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
	"net/http/httputil"
	"reflect"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
)

const GatewayPeer = "gateway.anvil-connect.internal"

func ConnectorPeer(id string) string { return id + ".connector.anvil-connect.internal" }

type binding struct {
	resource         config.Resource
	transport        *http.Transport
	upgradeTransport *http.Transport
	proxy            *httputil.ReverseProxy
	upgradeProxy     *httputil.ReverseProxy
}

type Dispatcher struct{ resources map[string]binding }

// NewDispatcher owns TLS configuration: there is no skip-verification or caller
// supplied dial/proxy option. Certificates must be deployment-issued credentials;
// the issuer, not a CSR, assigns the gateway/connector names and EKUs.
func NewDispatcher(declaration config.Gateway, roots *x509.CertPool, certificate tls.Certificate) (*Dispatcher, error) {
	if declaration.Validate() != nil || roots == nil || len(certificate.Certificate) == 0 || certificate.PrivateKey == nil {
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
	d := &Dispatcher{resources: map[string]binding{}}
	for _, resource := range declaration.Resources {
		if resource.Rule.Access != "api" {
			continue
		}
		resource.Rule.Methods = append([]string(nil), resource.Rule.Methods...)
		idle := time.Duration(resource.Rule.Limits.IdleSeconds) * time.Second
		tr := &http.Transport{
			Proxy: nil, DisableKeepAlives: true, DisableCompression: true,
			MaxConnsPerHost: resource.Rule.Limits.Concurrent, MaxResponseHeaderBytes: 65536,
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
				return relay.WrapConn(conn, idle), nil
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
			return nil
		}
		tr.Protocols.SetHTTP2(true)
		upgradeTransport := tr.Clone()
		upgradeTransport.Protocols = new(http.Protocols)
		upgradeTransport.Protocols.SetHTTP1(true)
		proxy := &httputil.ReverseProxy{
			Transport: tr, FlushInterval: -1, BufferPool: relay.NewBufferPool(resource.Rule.Limits), ErrorLog: log.New(io.Discard, "", 0),
			Rewrite: func(request *httputil.ProxyRequest) {
				request.Out.URL.Scheme, request.Out.URL.Host = "https", resource.TunnelAddress
				request.Out.Host = resource.Rule.Host
				request.Out.GetBody = nil
				httpedge.CleanAPIHeaders(request.Out.Header)
				request.Out.Header.Set(origin.ResourceHeader, resource.Rule.ID)
			},
			ModifyResponse: func(response *http.Response) error { return relay.APIResponse(response, resource.Rule) },
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
		d.resources[resource.Rule.ID] = binding{resource: resource, transport: tr, upgradeTransport: upgradeTransport, proxy: proxy, upgradeProxy: &upgradeProxy}
	}
	return d, nil
}

func (d *Dispatcher) Close() {
	for _, target := range d.resources {
		target.transport.CloseIdleConnections()
		target.upgradeTransport.CloseIdleConnections()
	}
}

// Dispatch is called only by the authenticated edge. It rechecks the immutable
// resource binding and holds the caller's context through the entire response.
func (d *Dispatcher) Dispatch(w http.ResponseWriter, r *http.Request, resource config.Resource, admitted access.Admission) {
	target, ok := d.resources[resource.Rule.ID]
	if !ok || !reflect.DeepEqual(resource, target.resource) || admitted.Resource != resource.Rule.ID || admitted.Method != r.Method || !resource.Rule.Allows(r.Host, r.URL.Path, r.Method) || httpedge.ValidateHead(r) != nil {
		http.Error(w, "transport resource denied", http.StatusForbidden)
		return
	}
	proxy := target.proxy
	if strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
		proxy = target.upgradeProxy
	}
	proxy.ServeHTTP(relay.Writer(w, r.Context(), time.Duration(resource.Rule.Limits.IdleSeconds)*time.Second), r)
}
