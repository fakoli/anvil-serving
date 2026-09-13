// Package browserfixture hosts the synthetic, loopback-only system used by the
// Playwright acceptance test. It deliberately uses the production Connect
// seams; fixture-only bypass modes are negative controls for the browser test.
package browserfixture

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/json"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/administration"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testidentity"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
	jose "github.com/go-jose/go-jose/v4"
	"golang.org/x/oauth2"
)

const (
	dashHost = "dash.example.test"
	idpHost  = "idp.example.test"
)

type oidcFixture struct {
	issuer        string
	key           *rsa.PrivateKey
	mu            sync.Mutex
	codeChallenge string
	nonce         string
	subject       string
}

func (o *oidcFixture) serveHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/.well-known/openid-configuration":
		_ = json.NewEncoder(w).Encode(map[string]any{"issuer": o.issuer, "authorization_endpoint": o.issuer + "/authorize", "token_endpoint": o.issuer + "/token", "jwks_uri": o.issuer + "/keys", "id_token_signing_alg_values_supported": []string{"RS256"}})
	case "/keys":
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": []jose.JSONWebKey{{Key: &o.key.PublicKey, KeyID: "browser-fixture", Algorithm: "RS256", Use: "sig"}}})
	case "/authorize":
		q := r.URL.Query()
		if q.Get("client_id") != "connect-browser" || q.Get("response_type") != "code" || q.Get("redirect_uri") != "https://"+dashHost+httpedge.BrowserCallbackPath || q.Get("state") == "" || q.Get("nonce") == "" || q.Get("code_challenge_method") != "S256" || q.Get("code_challenge") == "" {
			http.Error(w, "invalid synthetic authorization request", http.StatusBadRequest)
			return
		}
		o.mu.Lock()
		o.codeChallenge, o.nonce = q.Get("code_challenge"), q.Get("nonce")
		o.mu.Unlock()
		http.Redirect(w, r, "https://"+dashHost+httpedge.BrowserCallbackPath+"?state="+url.QueryEscape(q.Get("state"))+"&code=fixture-code", http.StatusFound)
	case "/token":
		if err := r.ParseForm(); err != nil {
			http.Error(w, "bad token form", http.StatusBadRequest)
			return
		}
		client, secret, basic := r.BasicAuth()
		o.mu.Lock()
		challenge, nonce, subject := o.codeChallenge, o.nonce, o.subject
		o.mu.Unlock()
		if !basic || client != "connect-browser" || secret != "fixture-secret" || r.Form.Get("grant_type") != "authorization_code" || r.Form.Get("code") != "fixture-code" || r.Form.Get("redirect_uri") != "https://"+dashHost+httpedge.BrowserCallbackPath || oauth2.S256ChallengeFromVerifier(r.Form.Get("code_verifier")) != challenge {
			http.Error(w, "synthetic exchange denied", http.StatusBadRequest)
			return
		}
		token, err := o.token(subject, nonce)
		if err != nil {
			http.Error(w, "synthetic signing failure", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "synthetic-access-token", "token_type": "Bearer", "expires_in": 3600, "id_token": token})
	default:
		http.NotFound(w, r)
	}
}

func (o *oidcFixture) token(subject, nonce string) (string, error) {
	signer, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.RS256, Key: o.key}, (&jose.SignerOptions{}).WithHeader("kid", "browser-fixture").WithType("JWT"))
	if err != nil {
		return "", err
	}
	payload, err := json.Marshal(map[string]any{"iss": o.issuer, "aud": "connect-browser", "sub": subject, "nonce": nonce, "iat": time.Now().Add(-time.Minute).Unix(), "exp": time.Now().Add(time.Hour).Unix()})
	if err != nil {
		return "", err
	}
	object, err := signer.Sign(payload)
	if err != nil {
		return "", err
	}
	return object.CompactSerialize()
}

type browserFixture struct {
	edge          atomic.Pointer[httpedge.Browser]
	idp           *oidcFixture
	sessionBypass atomic.Bool
	nativeBypass  atomic.Bool
	dispatcher    *transport.Dispatcher
	resource      config.Resource
}

func (f *browserFixture) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.Host {
	case idpHost:
		f.idp.serveHTTP(w, r)
	case dashHost:
		if f.sessionBypass.Load() {
			// Fixture-only negative control: make a missing Connect session check
			// observable without altering httpedge.Browser or production wiring.
			f.dispatcher.BrowserDispatch(w, r, f.resource, session.Admission{SessionID: "fixture-bypass", SessionGeneration: 1, Principal: "fixture", PrincipalGeneration: 1, Resource: f.resource.Rule.ID, Host: dashHost, Epoch: "fixture", ExpiresAt: time.Now().Add(time.Minute)})
			return
		}
		if edge := f.edge.Load(); edge != nil {
			edge.ServeHTTP(w, r)
		} else {
			http.Error(w, "fixture starting", 503)
		}
	default:
		http.NotFound(w, r)
	}
}

func browserGateway(t *testing.T, tunnel string) config.Gateway {
	t.Helper()
	return config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:17900", MaxConcurrent: 16, Resources: []config.Resource{{
		Rule:      config.Rule{ID: "dash", Host: dashHost, PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none", Limits: config.Limits{RequestBytes: 4096, Concurrent: 16, BufferBytes: 4096, IdleSeconds: 5, DurationSeconds: 20}},
		Connector: "origin-a", TunnelAddress: tunnel,
	}}}
}

func (f *browserFixture) nativeDashboard(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/":
		http.SetCookie(w, &http.Cookie{Name: "native_session", Value: "present", Path: "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteLaxMode})
		_, _ = io.WriteString(w, "<!doctype html><title>Connect dashboard</title><main id=dashboard>native dashboard</main>")
	case "/native-grant":
		cookie, err := r.Cookie("native_session")
		if err != nil || cookie.Value != "present" || r.Header.Get("Origin") != "https://"+dashHost || r.Header.Get("X-CSRF-Token") != "fixture-csrf" {
			http.Error(w, "native session or csrf denied", http.StatusForbidden)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	case "/fixture-native-guard":
		w.Header().Set("X-Native-Guard", "reached")
		if f.nativeBypass.Load() {
			// Only the native application's guard changes. Both versions still
			// pass through the same Connect session, transport and origin checks.
			w.WriteHeader(http.StatusNoContent)
			return
		}
		// A real dashboard control, intentionally stricter than Connect: the
		// negative control demonstrates that the browser test sees its removal.
		if _, err := r.Cookie("native_session"); err != nil || r.Header.Get("Origin") != "https://"+dashHost || r.Header.Get("X-CSRF-Token") != "fixture-csrf" {
			http.Error(w, "native session or csrf denied", http.StatusForbidden)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	case "/events":
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: ready\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	case "/ws":
		connection, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer connection.CloseNow()
		_, _, _ = connection.Read(r.Context())
	default:
		http.NotFound(w, r)
	}
}

func browserTLS(t *testing.T) (tls.Certificate, *x509.CertPool) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: dashHost}, DNSNames: []string{dashHost, idpHost}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	certificate := tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
	roots := x509.NewCertPool()
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	roots.AddCert(parsed)
	return certificate, roots
}

func TestBrowserFixture(t *testing.T) {
	if os.Getenv("ANVIL_CONNECT_BROWSER_FIXTURE") != "1" {
		t.Skip("launched only by Playwright")
	}
	ca := testpki.New(t)
	outerCert, outerRoots := browserTLS(t)
	outerLeaf, err := x509.ParseCertificate(outerCert.Certificate[0])
	if err != nil {
		t.Fatal(err)
	}
	spki := sha256.Sum256(outerLeaf.RawSubjectPublicKeyInfo)
	listener, err := tls.Listen("tcp4", "127.0.0.1:0", &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{outerCert}})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()

	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	issuerURL := "https://" + idpHost
	idp := &oidcFixture{issuer: issuerURL, key: key, subject: "allowed-subject"}
	fixture := &browserFixture{idp: idp}
	native := httptest.NewServer(http.HandlerFunc(fixture.nativeDashboard))
	defer native.Close()

	// The manager's owned OIDC client still validates TLS and issuer origin; its
	// dialer is the only synthetic-DNS seam and maps solely the fixture issuer.
	dialAddress := listener.Addr().String()
	oidcTransport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: outerRoots, ServerName: idpHost}, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != idpHost+":443" {
			return nil, &net.AddrError{Err: "fixture issuer destination denied", Addr: address}
		}
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", dialAddress)
	}}
	defer oidcTransport.CloseIdleConnections()
	state, err := store.Open(filepath.Join(t.TempDir(), "session"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer state.Close()

	// Build the native origin first so the real dispatcher has its fixed tunnel.
	placeholder := browserGateway(t, "127.0.0.1:17901")
	authority := testidentity.New(t, placeholder, ca)
	installation := authority.Installations["origin-a"]
	lease, err := access.NewLease(access.LeaseBinding{Installation: installation.ID, Resource: "dash", Epoch: installation.Epoch, Generation: installation.Generation}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := lease.Renew(lease.Binding(), 1, time.Now()); err != nil {
		t.Fatal(err)
	}
	envelope := config.Envelope{Rule: placeholder.Resources[0].Rule, Listen: "127.0.0.1:17902", OriginURL: native.URL}
	originProxy, err := origin.NewBrowser(envelope, transport.GatewayPeer, lease)
	if err != nil {
		t.Fatal(err)
	}
	defer originProxy.Close()
	inner := httptest.NewUnstartedServer(originProxy)
	inner.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{authority.Certificates["dash"]}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: ca.Roots}
	inner.EnableHTTP2 = true
	inner.StartTLS()
	defer inner.Close()
	gateway := browserGateway(t, inner.Listener.Addr().String())
	dispatcher, err := transport.NewDispatcher(gateway, ca.Roots, ca.Leaf(t, transport.GatewayPeer, true), authority.Issuer)
	if err != nil {
		t.Fatal(err)
	}
	defer dispatcher.Close()
	fixture.dispatcher, fixture.resource = dispatcher, gateway.Resources[0]
	server := &http.Server{Handler: fixture, ReadHeaderTimeout: 5 * time.Second, ErrorLog: log.New(os.Stderr, "browser fixture: ", 0)}
	go func() { _ = server.Serve(listener) }()
	defer server.Close()
	// Keep a direct probe here so fixture setup failures name the TLS/DNS seam
	// instead of being flattened into session.ErrUnavailable by OIDC discovery.
	probe, err := (&http.Client{Transport: oidcTransport, Timeout: 5 * time.Second}).Get(issuerURL + "/.well-known/openid-configuration")
	if err != nil {
		t.Fatalf("synthetic OIDC discovery probe: %v", err)
	}
	probe.Body.Close()
	if probe.StatusCode != http.StatusOK {
		t.Fatalf("synthetic OIDC discovery status: %d", probe.StatusCode)
	}
	manager, err := session.New(context.Background(), state, []config.Rule{gateway.Resources[0].Rule}, session.Config{Issuer: issuerURL, ClientID: "connect-browser", ClientSecret: "fixture-secret", CallbackPath: httpedge.BrowserCallbackPath, TransactionLifetime: session.DefaultTransactionLifetime, SessionLifetime: time.Hour, MaxTransactions: 8, MaxPerBrowser: 2, HTTPClient: &http.Client{Transport: oidcTransport}})
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close()
	member, err := manager.SetHuman(issuerURL, "allowed-subject", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	operator, err := manager.SetHuman(issuerURL, "operator-subject", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	gateway.BrowserAdministration = &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{operator.ID}}
	adminAuthority, err := administration.New(state, manager, gateway)
	if err != nil {
		t.Fatal(err)
	}
	edge, err := httpedge.NewBrowser(gateway, manager, dispatcher.BrowserDispatch)
	if err != nil {
		t.Fatal(err)
	}
	defer edge.Close()
	adminEdge, err := httpedge.NewAccessAdministration(gateway, adminAuthority)
	if err != nil || edge.SetAccessAdministration(adminEdge) != nil {
		t.Fatal("fixture administration setup failed", err)
	}
	fixture.edge.Store(edge)

	ready := map[string]string{"url": "https://" + dashHost, "spki": base64.StdEncoding.EncodeToString(spki[:]), "resolver": "127.0.0.1:" + strings.Split(dialAddress, ":")[1], "member_id": member.ID}
	if err := json.NewEncoder(os.Stdout).Encode(ready); err != nil {
		t.Fatal(err)
	}
	commands := fixtureCommands(os.Stdin)
	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, syscall.SIGTERM, os.Interrupt)
	defer signal.Stop(interrupt)
	for {
		select {
		case <-interrupt:
			return
		case received, open := <-commands:
			if !open {
				return
			}
			if received.err != nil {
				t.Error(errFixtureCommandInput)
				return
			}
			command := received.command
			switch command {
			case "subject allowed":
				idp.mu.Lock()
				idp.subject = "allowed-subject"
				idp.mu.Unlock()
			case "subject operator":
				idp.mu.Lock()
				idp.subject = "operator-subject"
				idp.mu.Unlock()
			case "subject denied":
				idp.mu.Lock()
				idp.subject = "denied-subject"
				idp.mu.Unlock()
			case "mode session-bypass":
				fixture.sessionBypass.Store(true)
			case "mode session-enforce":
				fixture.sessionBypass.Store(false)
			case "mode native-bypass":
				fixture.nativeBypass.Store(true)
			default:
				t.Errorf("unknown browser fixture command")
			}
			_ = json.NewEncoder(os.Stdout).Encode(map[string]string{"ack": command})
		}
	}
}
