package runtime

import (
	"context"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"path/filepath"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/browseridentity"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/control"
	"github.com/fakoli/anvil-serving/connect/internal/credential"
	"github.com/fakoli/anvil-serving/connect/internal/device"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/localhttp"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
	"github.com/fakoli/anvil-serving/connect/internal/tunnelgate"
)

var ErrUnavailable = errors.New("native runtime unavailable")

type SecretSource func(string) (string, bool)

func identitySigners(resources []config.Resource, secrets SecretSource) (map[string]*browseridentity.Signer, error) {
	result := map[string]*browseridentity.Signer{}
	for _, resource := range resources {
		if resource.Rule.NativeAuth != "signed-identity" {
			continue
		}
		secret, ok := secrets(resource.IdentityKeyEnv)
		if !ok {
			return nil, ErrUnavailable
		}
		signer, err := browseridentity.NewSigner(secret, resource.IdentityKeyID)
		if err != nil {
			return nil, ErrUnavailable
		}
		result[resource.Rule.ID] = signer
	}
	return result, nil
}

// Gateway owns the Unix ingress/admin listeners, authority database, and its
// pinned private tunnel child. Running is process state, not origin readiness.
type Gateway struct {
	cancel  context.CancelFunc
	close   sync.Once
	cleanup []func()
	done    chan struct{}
	errors  chan error
	wait    sync.WaitGroup
}

func (g *Gateway) Done() <-chan struct{} { return g.done }

func (g *Gateway) Close() {
	g.close.Do(func() {
		g.cancel()
		for i := len(g.cleanup) - 1; i >= 0; i-- {
			g.cleanup[i]()
		}
		g.wait.Wait()
		close(g.done)
	})
}

// Wait returns only a generic failure; child output and credential-bearing
// upstream errors never become lifecycle diagnostics.
func (g *Gateway) Wait(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return nil
	case <-g.done:
		return nil
	case <-g.errors:
		return ErrUnavailable
	}
}

func nativeServer(ctx context.Context, handler http.Handler) *http.Server {
	protocols := new(http.Protocols)
	protocols.SetHTTP1(true)
	protocols.SetUnencryptedHTTP2(true)
	return &http.Server{Handler: handler, Protocols: protocols, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 65536, ErrorLog: log.New(io.Discard, "", 0), BaseContext: func(net.Listener) context.Context { return ctx }}
}

func (g *Gateway) serve(server *http.Server, listener net.Listener) {
	g.cleanup = append(g.cleanup, func() {
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
		_ = server.Close()
		_ = listener.Close()
	})
	g.wait.Add(1)
	go func() {
		defer g.wait.Done()
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			select {
			case g.errors <- ErrUnavailable:
			default:
			}
		}
	}()
}

// StartGateway starts only validated, explicitly initialized private state.
// All public traffic must arrive over its same-UID Unix ingress behind the
// managed TLS edge. No TCP application/admin listener is opened here.
func StartGateway(parent context.Context, declaration GatewayConfig, secrets SecretSource) (_ *Gateway, result error) {
	if parent == nil || parent.Err() != nil || declaration.Validate() != nil || secrets == nil {
		return nil, ErrConfiguration
	}
	ctx, cancel := context.WithCancel(parent)
	g := &Gateway{cancel: cancel, done: make(chan struct{}), errors: make(chan error, 3)}
	defer func() {
		if result != nil {
			g.Close()
		}
	}()
	directory, err := privatefiles.Open(declaration.StateDirectory)
	if err != nil {
		return nil, ErrUnavailable
	}
	g.cleanup = append(g.cleanup, func() { _ = directory.Close() })
	innerCA, tunnelCA, err := loadAuthorities(directory)
	if err != nil {
		return nil, ErrUnavailable
	}
	state, err := store.Open(filepath.Join(declaration.StateDirectory, "authority"), nil)
	if err != nil {
		return nil, ErrUnavailable
	}
	g.cleanup = append(g.cleanup, func() { _ = state.Close() })
	var apiRules, browserRules []config.Rule
	var resourceIDs []string
	profiles := map[string]string{}
	for _, resource := range declaration.Gateway.Resources {
		resourceIDs = append(resourceIDs, resource.Rule.ID)
		profiles[resource.Rule.Host] = resource.Rule.Access
		if resource.Rule.Access == "api" {
			apiRules = append(apiRules, resource.Rule)
		} else {
			browserRules = append(browserRules, resource.Rule)
		}
	}
	identities, err := identity.NewManager(state, "https://"+declaration.ControlHost, resourceIDs)
	if err != nil {
		return nil, ErrUnavailable
	}
	issuer, err := credential.New(identities, state, declaration.Gateway, innerCA.certificate, innerCA.private)
	if err != nil {
		return nil, ErrUnavailable
	}
	leases, err := tunnelgate.NewLeases(declaration.Gateway, identities, nil)
	if err != nil {
		return nil, ErrUnavailable
	}
	controlHandler, err := control.NewServer(declaration.ControlHost, declaration.Gateway, identities, issuer, leases, nil)
	if err != nil {
		return nil, ErrUnavailable
	}
	g.cleanup = append(g.cleanup, controlHandler.Close)
	gatewayCertificate, err := innerCA.leaf(transport.GatewayPeer, transport.GatewayPeer, true)
	if err != nil {
		return nil, ErrUnavailable
	}
	dispatcher, err := transport.NewDispatcher(declaration.Gateway, innerCA.roots, gatewayCertificate, issuer)
	if err != nil {
		return nil, ErrUnavailable
	}
	g.cleanup = append(g.cleanup, dispatcher.Close)
	var keys *access.Keys
	var apiHandler *httpedge.Gateway
	if len(apiRules) != 0 {
		keys, err = access.NewKeys(state, apiRules)
		if err != nil {
			return nil, ErrUnavailable
		}
		apiHandler, err = httpedge.NewGateway(declaration.Gateway, keys, dispatcher.Dispatch)
		if err != nil {
			return nil, ErrUnavailable
		}
		g.cleanup = append(g.cleanup, apiHandler.Close)
	}
	var sessions *session.Manager
	var browserHandler *httpedge.Browser
	var devices *device.Authority
	if len(browserRules) != 0 {
		secret, ok := secrets(declaration.OIDC.ClientSecretEnv)
		if !ok || len(secret) < 1 || len(secret) > 4096 {
			return nil, ErrUnavailable
		}
		sessions, err = session.New(ctx, state, browserRules, session.Config{Issuer: declaration.OIDC.Issuer, ClientID: declaration.OIDC.ClientID, ClientSecret: secret, CallbackPath: httpedge.BrowserCallbackPath, TransactionLifetime: session.DefaultTransactionLifetime, SessionLifetime: session.DefaultSessionLifetime, MaxTransactions: 128, MaxPerBrowser: 8})
		if err != nil {
			return nil, ErrUnavailable
		}
		g.cleanup = append(g.cleanup, sessions.Close)
		if len(declaration.Gateway.DeviceAuthorizations) != 0 {
			devices, err = device.New(state, declaration.Gateway, sessions, keys)
			if err != nil {
				return nil, ErrUnavailable
			}
		}
		signers, signerErr := identitySigners(declaration.Gateway.Resources, secrets)
		if signerErr != nil {
			return nil, ErrUnavailable
		}
		browserHandler, err = httpedge.NewBrowserWithIdentityAndDevice(declaration.Gateway, sessions, signers, devices, dispatcher.BrowserDispatch)
		if err != nil {
			return nil, ErrUnavailable
		}
		g.cleanup = append(g.cleanup, browserHandler.Close)
	}
	pathToken, err := randomHex()
	if err != nil {
		return nil, ErrUnavailable
	}
	gateCertificate, err := tunnelCA.leaf(tunnelgate.GatePeer, pathToken, true)
	if err != nil {
		return nil, ErrUnavailable
	}
	backendCertificate, err := tunnelCA.leaf(tunnelgate.BackendPeer, tunnelgate.BackendPeer, false)
	if err != nil {
		return nil, ErrUnavailable
	}
	backends := map[string]tunnelgate.Backend{}
	for _, resource := range declaration.Gateway.Resources {
		token, err := randomHex()
		if err != nil {
			return nil, ErrUnavailable
		}
		backends[resource.Rule.ID] = tunnelgate.Backend{Address: declaration.TunnelListen, PathToken: pathToken, AuthToken: token}
	}
	gate, err := tunnelgate.New(declaration.TunnelHost, declaration.Gateway, leases, backends, tunnelCA.roots, gateCertificate)
	if err != nil {
		return nil, ErrUnavailable
	}
	g.cleanup = append(g.cleanup, gate.Close)
	restrictions, err := tunnelgate.Restrictions(declaration.Gateway, backends)
	if err != nil {
		return nil, ErrUnavailable
	}
	certificate, key, err := certificatePEM(backendCertificate)
	if err != nil {
		return nil, ErrUnavailable
	}
	files := map[string][]byte{"tunnel-server.cert": certificate, "tunnel-server.key": key, "tunnel-roots.pem": pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: tunnelCA.certificate.Raw}), "tunnel-restrictions.yaml": restrictions}
	paths := map[string]string{}
	for name, data := range files {
		if directory.Replace(name, data) != nil {
			return nil, ErrUnavailable
		}
		pin, err := directory.PinPath(name)
		if err != nil {
			return nil, ErrUnavailable
		}
		defer pin.Close() // StartServer opens each input before returning.
		paths[name] = pin.Path()
	}
	process, err := transport.StartServer(ctx, transport.ServerOptions{Binary: declaration.TunnelBinary, Listen: declaration.TunnelListen, CertificateFile: paths["tunnel-server.cert"], PrivateKeyFile: paths["tunnel-server.key"], ClientCAFile: paths["tunnel-roots.pem"], RestrictionsFile: paths["tunnel-restrictions.yaml"]})
	if err != nil {
		return nil, ErrUnavailable
	}
	g.cleanup = append(g.cleanup, func() { _ = process.Close() })
	adminHandler, err := admin.New(state, keys, identities, sessions, admin.Options{ControlHost: declaration.ControlHost, TunnelHost: declaration.TunnelHost, InnerCAPEM: string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: innerCA.certificate.Raw}))})
	if err != nil {
		return nil, ErrUnavailable
	}
	adminListener, err := localhttp.Listen(directory, "admin.sock")
	if err != nil {
		return nil, ErrUnavailable
	}
	g.serve(nativeServer(ctx, adminHandler), adminListener)
	ingress := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Host {
		case declaration.ControlHost:
			controlHandler.ServeHTTP(w, r)
		case declaration.TunnelHost:
			gate.ServeHTTP(w, r)
		default:
			switch profiles[r.Host] {
			case "api":
				apiHandler.ServeHTTP(w, r)
			case "browser":
				browserHandler.ServeHTTP(w, r)
			default:
				http.NotFound(w, r)
			}
		}
	})
	listener, err := localhttp.Listen(directory, "ingress.sock")
	if err != nil {
		return nil, ErrUnavailable
	}
	g.serve(nativeServer(ctx, ingress), listener)
	// A managed supervisor restarts the whole owned generation before the
	// ephemeral gateway/backend service certificates expire. This bounded
	// lifetime is explicit; it never silently serves with expired identities.
	g.wait.Add(1)
	go func() {
		defer g.wait.Done()
		leaf, _ := x509.ParseCertificate(gatewayCertificate.Certificate[0])
		timer := time.NewTimer(time.Until(leaf.NotAfter.Add(-time.Hour)))
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return
		case <-process.Done():
		case <-timer.C:
		}
		cancel()
		select {
		case g.errors <- ErrUnavailable:
		default:
		}
	}()
	return g, nil
}
