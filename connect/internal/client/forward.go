// Package client provides the fixed loopback SDK forwarder. It accepts one
// local credential and cannot select a public resource or destination itself.
package client

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
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

var ErrConfiguration = errors.New("invalid local forwarder configuration")

const localKeyPrefix = "acl1."

// SecretSource reads a provisioned Connect API key by its declared environment
// reference. The forwarder never uses ambient environment lookup itself.
type SecretSource func(name string) (string, bool)

// Options permits an owned test or private deployment root. A nil RootCAs uses
// the operating system trust store. It deliberately exposes no transport or
// dialer override.
type Options struct {
	RootCAs *x509.CertPool
}

// Forwarder is an http.Handler for one declared loopback listener and one API
// resource. The runtime owns opening the listener at Address.
type Forwarder struct {
	address          string
	rule             config.Rule
	localKey         [32]byte
	remoteKeyEnv     string
	secrets          SecretSource
	active           *access.Active
	slots            chan struct{}
	transport        *http.Transport
	upgradeTransport *http.Transport
	proxy            *httputil.ReverseProxy
	upgradeProxy     *httputil.ReverseProxy
}

type remoteKeyContext struct{}

// GenerateKey returns a caller-usable local forwarder key containing exactly
// 256 random bits. It is distinct from a remote Connect API key and must stay
// in local SDK configuration rather than in an HTTP request log.
func GenerateKey() (string, error) {
	var key [32]byte
	if _, err := rand.Read(key[:]); err != nil {
		return "", ErrConfiguration
	}
	return localKeyPrefix + base64.RawURLEncoding.EncodeToString(key[:]), nil
}

// ValidLocalKey validates the canonical credential without exposing its bytes.
func ValidLocalKey(raw string) bool {
	_, err := parseLocalKey(raw)
	return err == nil
}

func parseLocalKey(raw string) ([32]byte, error) {
	var key [32]byte
	if len(raw) != len(localKeyPrefix)+base64.RawURLEncoding.EncodedLen(len(key)) || !strings.HasPrefix(raw, localKeyPrefix) {
		return key, ErrConfiguration
	}
	decoded, err := base64.RawURLEncoding.DecodeString(strings.TrimPrefix(raw, localKeyPrefix))
	if err != nil || len(decoded) != len(key) || base64.RawURLEncoding.EncodeToString(decoded) != strings.TrimPrefix(raw, localKeyPrefix) {
		return key, ErrConfiguration
	}
	copy(key[:], decoded)
	return key, nil
}

// New constructs a handler for exactly listen (which must be a numeric IPv4
// loopback address). It fixes every admitted request to https://rule.Host and
// injects the remote Connect API key supplied by remoteKeyEnv only after local
// credential admission. It does not create a listener.
func New(rule config.Rule, listen, localKey, remoteKeyEnv string, secrets SecretSource, options Options) (*Forwarder, error) {
	return newForwarder(rule, listen, localKey, remoteKeyEnv, secrets, options, nil)
}

// newForwarder permits a loopback TLS fixture to supply a narrow dial function
// from tests. Production callers always use New, which leaves DialContext to
// the owned standard transport and cannot choose a runtime destination.
func newForwarder(rule config.Rule, listen, localKey, remoteKeyEnv string, secrets SecretSource, options Options, dial func(context.Context, string, string) (net.Conn, error)) (*Forwarder, error) {
	rule.Methods = append([]string(nil), rule.Methods...)
	if rule.Validate() != nil || rule.Access != "api" || rule.NativeAuth != "delegate-bearer" || !config.LoopbackAddress(listen) || !config.ValidEnv(remoteKeyEnv) || secrets == nil {
		return nil, ErrConfiguration
	}
	key, err := parseLocalKey(localKey)
	if err != nil {
		return nil, ErrConfiguration
	}
	upstream, err := url.Parse("https://" + rule.Host)
	if err != nil || upstream.Scheme != "https" || upstream.Host != rule.Host || upstream.Path != "" || upstream.RawQuery != "" || upstream.Fragment != "" || upstream.User != nil {
		return nil, ErrConfiguration
	}
	rootCAs := options.RootCAs
	if rootCAs != nil {
		rootCAs = rootCAs.Clone()
	}
	idle := time.Duration(rule.Limits.IdleSeconds) * time.Second
	transport := &http.Transport{
		Proxy:                  nil,
		DisableKeepAlives:      true,
		DisableCompression:     true,
		MaxConnsPerHost:        rule.Limits.Concurrent,
		MaxIdleConnsPerHost:    rule.Limits.Concurrent,
		MaxResponseHeaderBytes: 65536,
		TLSHandshakeTimeout:    5 * time.Second,
		ResponseHeaderTimeout:  idle,
		IdleConnTimeout:        idle,
		ForceAttemptHTTP2:      true,
		TLSClientConfig: &tls.Config{
			MinVersion: tls.VersionTLS13,
			ServerName: rule.Host,
			RootCAs:    rootCAs,
		},
	}
	transport.Protocols = new(http.Protocols)
	transport.Protocols.SetHTTP2(true)
	if dial == nil {
		dial = (&net.Dialer{Timeout: 5 * time.Second}).DialContext
	}
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != rule.Host+":443" {
			return nil, ErrConfiguration
		}
		conn, err := dial(ctx, network, address)
		if err != nil {
			return nil, err
		}
		return relay.WrapConn(conn, idle), nil
	}
	upgradeTransport := transport.Clone()
	upgradeTransport.Protocols = new(http.Protocols)
	upgradeTransport.Protocols.SetHTTP1(true)
	active, err := access.NewActive(rule.Limits.Concurrent, 250*time.Millisecond)
	if err != nil {
		transport.CloseIdleConnections()
		return nil, ErrConfiguration
	}
	f := &Forwarder{address: listen, rule: rule, localKey: key, remoteKeyEnv: remoteKeyEnv, secrets: secrets, active: active, slots: make(chan struct{}, rule.Limits.Concurrent), transport: transport, upgradeTransport: upgradeTransport}
	f.proxy = &httputil.ReverseProxy{
		Transport:     transport,
		FlushInterval: -1,
		BufferPool:    relay.NewBufferPool(rule.Limits),
		ErrorLog:      log.New(io.Discard, "", 0),
		Rewrite: func(request *httputil.ProxyRequest) {
			request.Out.URL.Scheme = upstream.Scheme
			request.Out.URL.Host = upstream.Host
			request.Out.Host = rule.Host
			request.Out.GetBody = nil // admitted bodies are never replayable.
			httpedge.CleanAPIHeaders(request.Out.Header)
			// Identify the HTTPS client that actually contacts the public edge.
			// Local SDK metadata must not impersonate a different remote client.
			request.Out.Header.Set("User-Agent", "anvil-connect/1")
			// The public API edge uses the ordinary SDK bearer carrier. This
			// replaces, rather than forwards, the local loopback credential.
			request.Out.Header.Set("Authorization", "Bearer "+request.In.Context().Value(remoteKeyContext{}).(string))
		},
		ModifyResponse: func(response *http.Response) error {
			return relay.APIResponse(response, rule)
		},
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, err error) {
			status := http.StatusBadGateway
			var bodyLimit *http.MaxBytesError
			if errors.As(err, &bodyLimit) {
				status = http.StatusRequestEntityTooLarge
			}
			forwardFailure(w, status)
		},
	}
	upgradeProxy := *f.proxy
	upgradeProxy.Transport = upgradeTransport
	f.upgradeProxy = &upgradeProxy
	return f, nil
}

// Address is the exact loopback address the runtime must bind before exposing
// Handler. Requests with another Host are denied before credential lookup.
func (f *Forwarder) Address() string { return f.address }

func (f *Forwarder) Handler() http.Handler { return f }

// Close denies future local admissions, cancels live streams, and releases
// only the idle connections owned by this forwarder.
func (f *Forwarder) Close() {
	if f == nil {
		return
	}
	if f.active != nil {
		f.active.Close()
	}
	if f.transport != nil {
		f.transport.CloseIdleConnections()
	}
	if f.upgradeTransport != nil {
		f.upgradeTransport.CloseIdleConnections()
	}
}

func forwardFailure(w http.ResponseWriter, status int) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if status == http.StatusUnauthorized {
		w.Header().Set("WWW-Authenticate", `Bearer realm="anvil-connect-local"`)
	}
	if status == http.StatusTooManyRequests {
		w.Header().Set("Retry-After", "1")
	}
	http.Error(w, http.StatusText(status), status)
}

func localCredential(r *http.Request, expected [32]byte) bool {
	raw, err := httpedge.APIKey(r)
	if err != nil {
		return false
	}
	provided, err := parseLocalKey(raw)
	return err == nil && subtle.ConstantTimeCompare(expected[:], provided[:]) == 1
}

func acquire(slots chan struct{}) bool {
	select {
	case slots <- struct{}{}:
		return true
	default:
		return false
	}
}

func usableRemoteKey(value string) bool {
	return value != "" && len(value) <= 4096 && strings.TrimSpace(value) == value && !strings.ContainsAny(value, "\r\n\x00\t ")
}

func (f *Forwarder) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if f == nil || r == nil || r.Host != f.address {
		forwardFailure(w, http.StatusBadRequest)
		return
	}
	// ValidateHead deliberately expects a public hostname. Preserve the request
	// target but substitute the fixed resource host only for structural policy.
	checked := r.Clone(r.Context())
	checked.Host = f.rule.Host
	if httpedge.ValidateHead(checked) != nil || !f.rule.Allows(f.rule.Host, checked.URL.Path, checked.Method) {
		forwardFailure(w, http.StatusBadRequest)
		return
	}
	if checked.ContentLength > f.rule.Limits.RequestBytes {
		forwardFailure(w, http.StatusRequestEntityTooLarge)
		return
	}
	if !localCredential(checked, f.localKey) {
		forwardFailure(w, http.StatusUnauthorized)
		return
	}
	if !acquire(f.slots) {
		forwardFailure(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-f.slots }()
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(f.rule.Limits.DurationSeconds)*time.Second)
	defer cancel()
	ctx, release, err := f.active.Watch(ctx, func() error { return nil })
	if err != nil {
		forwardFailure(w, http.StatusServiceUnavailable)
		return
	}
	defer release()
	remoteKey, ok := f.secrets(f.remoteKeyEnv)
	if !ok || !usableRemoteKey(remoteKey) {
		forwardFailure(w, http.StatusServiceUnavailable)
		return
	}
	clean := r.Clone(context.WithValue(ctx, remoteKeyContext{}, remoteKey))
	clean.Host = f.rule.Host
	clean.Body = relay.Body(w, http.MaxBytesReader(w, r.Body, f.rule.Limits.RequestBytes), ctx, time.Duration(f.rule.Limits.IdleSeconds)*time.Second)
	defer clean.Body.Close()
	proxy := f.proxy
	if strings.EqualFold(clean.Header.Get("Upgrade"), "websocket") {
		proxy = f.upgradeProxy
	}
	proxy.ServeHTTP(relay.Writer(w, ctx, time.Duration(f.rule.Limits.IdleSeconds)*time.Second), clean)
}
