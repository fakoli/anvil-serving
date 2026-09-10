package browserfixture

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base32"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	stdruntime "runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	connectruntime "github.com/fakoli/anvil-serving/connect/internal/runtime"
)

const (
	edgeControlHost = "control.example.test"
	edgeTunnelHost  = "tunnel.example.test"
	edgeAPIHost     = "api.example.test"
)

// runtimeCaddyConfig is the same public split used by the managed renderer:
// the tunnel and resource upgrades take HTTP/1.1, while ordinary browser, API,
// and control requests use h2c over the gateway's same-UID ingress socket.
func runtimeCaddyConfig(listen, authListen, socket, certificate, key string) map[string]any {
	headers := []string{"Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP", "X-Anvil-Connect-User", "X-Anvil-Connect-Groups", "X-Auth-Request-User", "X-Auth-Request-Email", "X-Authenticated-User", "X-Authenticated-Groups", "Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name", "Cf-Access-Jwt-Assertion", "X-Goog-Authenticated-User", "X-Goog-Authenticated-User-Email", "X-Amzn-Oidc-Data", "X-Amzn-Oidc-Identity", "X-Amzn-Oidc-Accesstoken", "Tailscale-User-Login"}
	clean := map[string]any{"handler": "headers", "request": map[string]any{"delete": headers}}
	proxy := func(destination string, versions []string) map[string]any {
		return map[string]any{"handler": "reverse_proxy", "upstreams": []any{map[string]any{"dial": destination}}, "transport": map[string]any{"protocol": "http", "versions": versions}}
	}
	route := func(match map[string]any, versions []string) map[string]any {
		return map[string]any{"match": []any{match}, "handle": []any{clean, proxy("unix/"+socket, versions)}}
	}
	upgrade := map[string]any{"Connection": map[string]any{"pattern": `(?i)(^|,)[\t ]*upgrade[\t ]*(,|$)`}, "Upgrade": map[string]any{"pattern": `(?i)^websocket$`}}
	tunnelMatch := map[string]any{"host": []string{edgeTunnelHost}, "method": []string{"GET"}, "path": []string{"/acv1/events"}, "header_regexp": upgrade}
	browserUpgrade := map[string]any{"host": []string{dashHost}, "path": []string{"/", "/*"}, "header_regexp": upgrade}
	apiUpgrade := map[string]any{"host": []string{edgeAPIHost}, "path": []string{"/v1", "/v1/*"}, "header_regexp": upgrade}
	return map[string]any{
		"admin": map[string]any{"disabled": true},
		"apps": map[string]any{
			"http": map[string]any{"servers": map[string]any{"anvil_connect": map[string]any{
				"listen": []string{listen}, "tls_connection_policies": []any{map[string]any{}},
				"automatic_https": map[string]any{"disable_redirects": true, "disable_certificates": true},
				"routes": []any{
					map[string]any{"match": []any{map[string]any{"host": []string{edgeAuthHost}}}, "handle": []any{clean, proxy(authListen, []string{"1.1"})}},
					route(tunnelMatch, []string{"1.1"}),
					route(browserUpgrade, []string{"1.1"}),
					route(map[string]any{"host": []string{dashHost}, "path": []string{"/", "/*"}, "method": []string{"GET", "POST"}}, []string{"h2c"}),
					route(apiUpgrade, []string{"1.1"}),
					route(map[string]any{"host": []string{edgeAPIHost}, "path": []string{"/v1", "/v1/*"}, "method": []string{"GET", "POST"}}, []string{"h2c"}),
					route(map[string]any{"host": []string{edgeControlHost}}, []string{"h2c"}),
					map[string]any{"handle": []any{map[string]any{"handler": "static_response", "status_code": 404}}},
				},
			}}},
			"tls": map[string]any{"certificates": map[string]any{"load_files": []any{map[string]any{"certificate": certificate, "key": key}}}, "disable_storage_clean": true},
		},
	}
}

type runtimeProxyObserver struct {
	mu     sync.Mutex
	counts map[string]int
}

func (o *runtimeProxyObserver) observe(host string) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.counts[host]++
}

func (o *runtimeProxyObserver) seen(host string) bool {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.counts[host] > 0
}

func runtimeConnectProxy(target string, allowed map[string]bool, observer *runtimeProxyObserver) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if observer != nil {
			observer.observe(r.Host)
		}
		if r.Method != http.MethodConnect || !allowed[r.Host] {
			http.Error(w, "denied", http.StatusForbidden)
			return
		}
		upstream, err := net.DialTimeout("tcp4", target, time.Second)
		if err != nil {
			http.Error(w, "upstream", http.StatusBadGateway)
			return
		}
		defer upstream.Close()
		downstream, buffered, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer downstream.Close()
		if _, err := buffered.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil || buffered.Flush() != nil {
			return
		}
		done := make(chan struct{})
		go func() { _, _ = io.Copy(upstream, buffered); _ = upstream.Close(); close(done) }()
		_, _ = io.Copy(downstream, upstream)
		<-done
	})
}

func runtimeTunnelBinary(t *testing.T) string {
	t.Helper()
	_, source, _, ok := stdruntime.Caller(0)
	if !ok {
		t.Fatal("transport lock source unavailable")
	}
	file, err := os.Open(filepath.Join(filepath.Dir(source), "..", "transport.lock.json"))
	if err != nil {
		t.Fatal("transport lock unavailable")
	}
	defer file.Close()
	var lock struct {
		Schema    string `json:"schema"`
		Name      string `json:"name"`
		Version   string `json:"version"`
		Artifacts map[string]struct {
			BinarySHA256 string `json:"binary_sha256"`
		} `json:"artifacts"`
	}
	if err := json.NewDecoder(io.LimitReader(file, 64*1024)).Decode(&lock); err != nil || lock.Schema != "anvil-connect.transport-lock/v1" || lock.Name != "wstunnel" || lock.Version != "10.7.1" {
		t.Fatal("invalid transport lock")
	}
	expected := lock.Artifacts[stdruntime.GOOS+"/"+stdruntime.GOARCH].BinarySHA256
	if len(expected) != 64 {
		t.Fatal("transport digest unavailable")
	}
	if _, err := hex.DecodeString(expected); err != nil {
		t.Fatal("transport digest invalid")
	}
	path := os.Getenv("ANVIL_CONNECT_WSTUNNEL")
	if path == "" {
		path = "/data/cache/anvil-connect/tools/wstunnel/10.7.1/linux-amd64/wstunnel"
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&0111 == 0 || info.Size() < 1 || info.Size() > 32*1024*1024 {
		t.Fatal("pinned wstunnel binary is unavailable")
	}
	binary, err := os.Open(path)
	if err != nil {
		t.Fatal("pinned wstunnel binary is unavailable")
	}
	defer binary.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, io.LimitReader(binary, 32*1024*1024+1)); err != nil || hex.EncodeToString(hash.Sum(nil)) != expected {
		t.Fatal("pinned wstunnel binary digest mismatch")
	}
	return path
}

// TestBrowserRuntimeEdgeFixture composes the public Caddy/Authelia edge with
// the actual gateway and connector lifecycle. It is isolated in one Go test
// process because it temporarily supplies the gateway's strict OIDC transport.
func TestBrowserRuntimeEdgeFixture(t *testing.T) {
	passkeyFixture := os.Getenv("ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE") == "1"
	deviceFixture := passkeyFixture || os.Getenv("ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE") == "1"
	if os.Getenv("ANVIL_CONNECT_BROWSER_RUNTIME_EDGE_FIXTURE") != "1" && !deviceFixture {
		t.Skip("launched only by the runtime-edge Playwright test")
	}
	caddy, authelia := edgeTool(t, "caddy"), edgeTool(t, "authelia")
	binary := runtimeTunnelBinary(t)
	directory := t.TempDir()
	secrets, state, childHome := filepath.Join(directory, "secrets"), filepath.Join(directory, "state"), filepath.Join(directory, "child-home")
	for _, path := range []string{secrets, state, childHome} {
		if err := os.Mkdir(path, 0700); err != nil {
			t.Fatal(err)
		}
	}
	certificate, key, root, roots := edgeCertificate(t, edgeAuthHost, dashHost, edgeAPIHost, edgeControlHost, edgeTunnelHost)
	certificatePath, keyPath, rootPath := filepath.Join(secrets, "edge.pem"), filepath.Join(secrets, "edge.key"), filepath.Join(secrets, "edge-root.pem")
	edgeWrite(t, certificatePath, certificate)
	edgeWrite(t, keyPath, key)
	edgeWrite(t, rootPath, root)
	edgePrepareHome(t, childHome)
	allowedPassword, allowedDigest := edgeHash(t, childHome, authelia)
	clientSecret, clientDigest := edgeHash(t, childHome, authelia)
	users := filepath.Join(secrets, "users.yml")
	edgeWrite(t, users, "users:\n  fixture-allowed:\n    displayname: Fixture Allowed\n    password: "+jsonString(allowedDigest)+"\n    email: fixture-allowed@example.test\n    groups: []\n")
	clientSecretPath := filepath.Join(secrets, "client-secret")
	edgeWrite(t, clientSecretPath, clientDigest+"\n")
	secretPaths := map[string]string{}
	for _, name := range []string{"session", "storage", "validation", "hmac"} {
		path := filepath.Join(secrets, name)
		edgeWrite(t, path, edgeRandom(t, 48)+"\n")
		secretPaths[name] = path
	}
	// The OIDC signing key is intentionally distinct from the TLS fixture CA.
	oidcKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	oidcDER, err := x509.MarshalPKCS8PrivateKey(oidcKey)
	if err != nil {
		t.Fatal(err)
	}
	oidcPath := filepath.Join(secrets, "oidc.pem")
	edgeWrite(t, oidcPath, string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: oidcDER})))
	authListen, caddyListen := edgeReserve(t), edgeReserve(t)
	if deviceFixture {
		// The P02 container owns this loopback-only port. The real CLI has no
		// dial override, so it must reach the normal HTTPS authority on 443.
		caddyListen = "127.0.0.1:443"
	}
	authConfig := filepath.Join(directory, "authelia.yml")
	edgeWrite(t, authConfig, runtimeAutheliaConfig(authListen, state, users, clientSecretPath, secretPaths["session"], secretPaths["storage"], secretPaths["validation"], secretPaths["hmac"], oidcPath, passkeyFixture))
	edgeRun(t, childHome, authelia, "storage", "migrate", "up", "--config", authConfig, "--config.experimental.filters", "template")
	allowedTOTP := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString([]byte(edgeRandom(t, 20)))
	edgeRun(t, childHome, authelia, "storage", "user", "totp", "generate", "fixture-allowed", "--secret", allowedTOTP, "--issuer", "Anvil Connect Fixture", "--algorithm", "SHA1", "--digits", "6", "--period", "30", "--config", authConfig, "--config.experimental.filters", "template")
	if deviceFixture {
		// The device binding must name the same opaque OIDC subject that the
		// later browser login presents. Create it through Authelia's storage
		// authority before exporting it; no gateway map is synthesized.
		edgeRun(t, childHome, authelia, "storage", "user", "identifiers", "add", "fixture-allowed", "--config", authConfig, "--config.experimental.filters", "template")
	}
	startEdgeChild(t, childHome, authelia, "--config", authConfig, "--config.experimental.filters", "template")
	allowedSubject, deviceHuman := "", ""
	if deviceFixture {
		deviceSubject, exportErr := edgeGrantedSubject(childHome, authelia, authConfig, filepath.Join(directory, "device-identifiers.yml"))
		if exportErr != nil {
			t.Fatal("fixture device subject export failed")
		}
		allowedSubject = deviceSubject
		deviceHuman = runtimeFixtureHumanID("https://"+edgeAuthHost, deviceSubject)
		if deviceHuman == "" {
			t.Fatal("fixture device human id unavailable")
		}
	}

	resources := []config.Resource{{Rule: config.Rule{ID: "dash", Host: dashHost, PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none", Limits: config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: 5, DurationSeconds: 20}}, Connector: "connector-a", TunnelAddress: edgeReserve(t)}}
	if deviceFixture {
		resources = append(resources, config.Resource{Rule: config.Rule{ID: "router", Host: edgeAPIHost, PathPrefix: "/v1", Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer", Limits: config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: 5, DurationSeconds: 20}}, Connector: "connector-a", TunnelAddress: edgeReserve(t)})
	}
	gatewayDeclaration := config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: edgeReserve(t), MaxConcurrent: 4, Resources: resources}
	operatorHuman := ""
	if deviceFixture {
		gatewayDeclaration.DeviceAuthorizations = []config.DeviceAuthorization{{BrowserResource: "dash", APIResource: "router", Methods: []string{"GET"}, Label: "Fixture terminal", Principals: map[string]string{deviceHuman: "fixture-sdk"}}}
	}
	if passkeyFixture {
		operatorHuman = runtimeFixtureHumanID("https://"+edgeAuthHost, "fixture-operator")
		if operatorHuman == "" || operatorHuman == deviceHuman {
			t.Fatal("fixture browser administrator is unavailable")
		}
		gatewayDeclaration.BrowserAdministration = &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{operatorHuman}}
	}
	gatewayCfg := connectruntime.GatewayConfig{Schema: "anvil-connect.gateway-runtime/v1", Gateway: gatewayDeclaration, ControlHost: edgeControlHost, TunnelHost: edgeTunnelHost, StateDirectory: filepath.Join(directory, "gateway"), TunnelBinary: binary, TunnelListen: edgeReserve(t), OIDC: connectruntime.OIDC{Issuer: "https://" + edgeAuthHost, ClientID: "connect-browser", ClientSecretEnv: "OIDC_CLIENT_SECRET"}}
	caddyConfig, err := json.Marshal(runtimeCaddyConfig(caddyListen, authListen, filepath.Join(gatewayCfg.StateDirectory, "ingress.sock"), certificatePath, keyPath))
	if err != nil {
		t.Fatal(err)
	}
	caddyConfigPath := filepath.Join(directory, "caddy.json")
	edgeWrite(t, caddyConfigPath, string(caddyConfig))
	startEdgeChild(t, childHome, caddy, "run", "--config", caddyConfigPath)

	probeTransport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots, ServerName: edgeAuthHost}, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != edgeAuthHost+":443" {
			return nil, &net.AddrError{Err: "fixture issuer destination denied", Addr: address}
		}
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", caddyListen)
	}}
	defer probeTransport.CloseIdleConnections()
	deadline := time.Now().Add(15 * time.Second)
	for {
		response, probeErr := (&http.Client{Transport: probeTransport, Timeout: 2 * time.Second}).Get("https://" + edgeAuthHost + "/api/health")
		if probeErr == nil && response.StatusCode == http.StatusOK {
			response.Body.Close()
			break
		}
		if response != nil {
			response.Body.Close()
		}
		if time.Now().After(deadline) {
			t.Fatal("pinned Authelia and Caddy edge did not become healthy")
		}
		time.Sleep(100 * time.Millisecond)
	}

	if err := connectruntime.InitializeGateway(gatewayCfg); err != nil {
		t.Fatal(err)
	}
	// session.New clones DefaultTransport when runtime wiring supplies no custom
	// client. This clone preserves TLS 1.3 and fixture-root verification while
	// mapping only the declared issuer hostname to this fixture Caddy listener.
	originalDefault := http.DefaultTransport
	base, ok := originalDefault.(*http.Transport)
	if !ok {
		t.Fatal("default HTTP transport is not cloneable")
	}
	issuerTransport := base.Clone()
	issuerTransport.Proxy = nil
	issuerTransport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots.Clone(), ServerName: edgeAuthHost}
	issuerTransport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != edgeAuthHost+":443" {
			return nil, &net.AddrError{Err: "fixture issuer destination denied", Addr: address}
		}
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", caddyListen)
	}
	http.DefaultTransport = issuerTransport
	t.Cleanup(func() { http.DefaultTransport = originalDefault; issuerTransport.CloseIdleConnections() })
	gateway, err := connectruntime.StartGateway(context.Background(), gatewayCfg, func(name string) (string, bool) { return clientSecret, name == "OIDC_CLIENT_SECRET" })
	if err != nil {
		t.Fatal(err)
	}
	defer gateway.Close()

	// Prove the rendered narrow tunnel route reaches the real gate over h1.
	// Its deliberately invalid bearer must be rejected by the gate, not Caddy.
	tunnelTransport := &http.Transport{ForceAttemptHTTP2: false, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots.Clone(), ServerName: edgeTunnelHost}, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != edgeTunnelHost+":443" {
			return nil, &net.AddrError{Err: "fixture tunnel destination denied", Addr: address}
		}
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", caddyListen)
	}}
	tunnelRequest, err := http.NewRequest(http.MethodGet, "https://"+edgeTunnelHost+"/acv1/events", nil)
	if err != nil {
		t.Fatal(err)
	}
	tunnelRequest.Header.Set("Connection", "Upgrade")
	tunnelRequest.Header.Set("Upgrade", "websocket")
	tunnelRequest.Header.Set("Sec-WebSocket-Version", "13")
	tunnelRequest.Header.Set("Sec-WebSocket-Key", runtimeWebSocketKey(t))
	tunnelRequest.Header.Set("Sec-WebSocket-Protocol", "v1")
	tunnelRequest.Header.Set("Authorization", "Bearer invalid")
	tunnelResponse, err := (&http.Client{Transport: tunnelTransport, Timeout: 5 * time.Second}).Do(tunnelRequest)
	tunnelTransport.CloseIdleConnections()
	if err != nil {
		t.Fatal("public tunnel route unavailable")
	}
	io.Copy(io.Discard, tunnelResponse.Body)
	tunnelResponse.Body.Close()
	if tunnelResponse.StatusCode != http.StatusUnauthorized {
		t.Fatal("public tunnel route did not reach gate")
	}

	gatewayDirectory, err := privatefiles.Open(gatewayCfg.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer gatewayDirectory.Close()
	adminPin, err := gatewayDirectory.PinPath("admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer adminPin.Close()
	if deviceFixture {
		if _, err := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "principal-set", Principal: "fixture-sdk", Grants: []access.Grant{{Resource: "router", Methods: []string{"GET"}}}}); err != nil {
			t.Fatal("fixture API principal setup failed")
		}
	}
	if passkeyFixture {
		operator, operatorErr := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "human-set", Issuer: "https://" + edgeAuthHost, Subject: "fixture-operator", Resources: []string{"dash"}})
		if operatorErr != nil || operator.Principal != operatorHuman || operator.Principal == deviceHuman {
			t.Fatal("fixture browser administrator setup failed")
		}
	}
	nativeFixture := &edgeFixture{}
	var eventsStarted, eventsClosed, wsStarted, wsClosed atomic.Int64
	native := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/events":
			eventsStarted.Add(1)
			defer eventsClosed.Add(1)
		case "/ws":
			wsStarted.Add(1)
			defer wsClosed.Add(1)
		}
		nativeFixture.nativeDashboard(w, r)
	}))
	defer native.Close()
	var apiRequests, apiPosts, apiEventsStarted, apiEventsClosed, apiWSStarted, apiWSClosed atomic.Int64
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		apiRequests.Add(1)
		if r.Method == http.MethodPost {
			apiPosts.Add(1)
		}
		if !runtimeNativeAPIRequest(r) {
			http.Error(w, "fixture API denied", http.StatusUnauthorized)
			return
		}
		switch r.URL.Path {
		case "/v1/models":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"object":"list","data":[{"id":"fixture-model"}]}`)
		case "/v1/events":
			apiEventsStarted.Add(1)
			defer apiEventsClosed.Add(1)
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "data: ready\n\n")
			w.(http.Flusher).Flush()
			<-r.Context().Done()
		case "/v1/ws":
			connection, err := websocket.Accept(w, r, nil)
			if err != nil {
				return
			}
			apiWSStarted.Add(1)
			defer apiWSClosed.Add(1)
			defer connection.CloseNow()
			_, _, _ = connection.Read(r.Context())
		default:
			http.NotFound(w, r)
		}
	}))
	defer api.Close()
	proxyObserver := &runtimeProxyObserver{counts: map[string]int{}}
	proxyServer := httptest.NewServer(runtimeConnectProxy(caddyListen, map[string]bool{edgeControlHost + ":443": true, edgeTunnelHost + ":443": true}, proxyObserver))
	defer proxyServer.Close()
	connectorResources := []connectruntime.ConnectorResource{{Envelope: config.Envelope{Rule: gatewayCfg.Gateway.Resources[0].Rule, Listen: edgeReserve(t), OriginURL: native.URL}, ReverseAddress: gatewayCfg.Gateway.Resources[0].TunnelAddress}}
	inviteResources := []string{"dash"}
	if deviceFixture {
		connectorResources = append(connectorResources, connectruntime.ConnectorResource{Envelope: config.Envelope{Rule: gatewayCfg.Gateway.Resources[1].Rule, Listen: edgeReserve(t), OriginURL: api.URL, TokenEnv: "ANVIL_CONNECT_FIXTURE_API_TOKEN"}, ReverseAddress: gatewayCfg.Gateway.Resources[1].TunnelAddress})
		inviteResources = append(inviteResources, "router")
	}
	connectorCfg := connectruntime.ConnectorConfig{Schema: "anvil-connect.connector-runtime/v1", ID: "connector-a", ControlHost: edgeControlHost, TunnelHost: edgeTunnelHost, StateDirectory: filepath.Join(directory, "connector"), TunnelBinary: binary, PublicTrustFile: rootPath, HTTPProxyURL: proxyServer.URL, Resources: connectorResources}
	invite, err := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "invite", Installation: "connector-a", Role: "connector", Resources: inviteResources, LifetimeSeconds: 60})
	if err != nil {
		t.Fatal(err)
	}
	if err := connectruntime.InitializeConnector(context.Background(), connectorCfg, invite); err != nil {
		t.Fatal(err)
	}
	identity, err := connectruntime.ConnectorIdentity(connectorCfg)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "approve", Installation: "connector-a", Fingerprint: identity.Fingerprint}); err != nil {
		t.Fatal(err)
	}
	connector, err := connectruntime.StartConnector(context.Background(), connectorCfg, func(name string) (string, bool) {
		return "fixture-native-api-token", deviceFixture && name == "ANVIL_CONNECT_FIXTURE_API_TOKEN"
	})
	if err != nil {
		t.Fatal(err)
	}
	defer connector.Close()
	// StartConnector reports process ownership, not reverse-tunnel readiness.
	// Observe the exact CONNECT authority and then the gateway-side reverse
	// listener, with each retry driven by the actual declared resource binding.
	deadline = time.Now().Add(10 * time.Second)
	for !proxyObserver.seen(edgeTunnelHost+":443") && time.Now().Before(deadline) {
		select {
		case <-connector.Done():
			t.Fatal("connector tunnel child exited before public CONNECT")
		default:
		}
		time.Sleep(50 * time.Millisecond)
	}
	if !proxyObserver.seen(edgeTunnelHost + ":443") {
		t.Fatal("connector did not CONNECT to declared tunnel host")
	}
	for {
		connection, dialErr := net.DialTimeout("tcp4", gatewayCfg.Gateway.Resources[0].TunnelAddress, 200*time.Millisecond)
		if dialErr == nil {
			connection.Close()
			break
		}
		select {
		case <-connector.Done():
			t.Fatal("connector tunnel child exited before reverse listener")
		default:
		}
		if time.Now().After(deadline) {
			t.Fatal("connector reverse listener did not become ready")
		}
		time.Sleep(50 * time.Millisecond)
	}

	ready := map[string]string{"url": "https://" + dashHost, "resolver": caddyListen, "ca": rootPath, "allowed_user": "fixture-allowed", "allowed_password": allowedPassword}
	if deviceFixture {
		clientHome, localKeyPath := runtimeFixtureClient(t, directory)
		ready["client_home"] = clientHome
		ready["local_key"] = localKeyPath
		ready["local_base_url"] = "http://127.0.0.1:8787/v1"
	}
	if passkeyFixture {
		ready["passkey_fixture"] = "enabled"
	}
	if err := json.NewEncoder(os.Stdout).Encode(ready); err != nil {
		t.Fatal(err)
	}
	commands := make(chan string)
	go func() {
		defer close(commands)
		buffer := make([]byte, 256)
		for {
			n, readErr := os.Stdin.Read(buffer)
			if n > 0 {
				for _, line := range strings.Split(string(buffer[:n]), "\n") {
					if strings.TrimSpace(line) != "" {
						commands <- strings.TrimSpace(line)
					}
				}
			}
			if readErr != nil {
				return
			}
		}
	}()
	for command := range commands {
		response := map[string]string{"ack": command}
		switch command {
		case "grant allowed", "disable allowed":
			if allowedSubject == "" {
				subject, exportErr := edgeGrantedSubject(childHome, authelia, authConfig, filepath.Join(directory, "identifiers.yml"))
				if exportErr != nil {
					response["error"] = "fixture subject export failed"
				} else {
					allowedSubject = subject
				}
			}
			if response["error"] == "" {
				if _, grantErr := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "human-set", Issuer: "https://" + edgeAuthHost, Subject: allowedSubject, Resources: []string{"dash"}, Disabled: command == "disable allowed"}); grantErr != nil {
					response["error"] = "fixture Connect grant failed"
				}
			}
		case "totp allowed":
			response["code"] = edgeStableTOTP(allowedTOTP)
		case "elevation code":
			if !passkeyFixture {
				response["error"] = "passkey fixture is disabled"
				break
			}
			code, codeErr := runtimeFixtureElevationCode(filepath.Join(state, "notifications.txt"))
			if codeErr != nil {
				response["error"] = "fixture elevation code is unavailable"
			} else {
				response["code"] = code
			}
		case "api request count":
			response["count"] = strconv.FormatInt(apiRequests.Load(), 10)
		case "api post count":
			response["count"] = strconv.FormatInt(apiPosts.Load(), 10)
		case "browser stream counts":
			response["events_started"] = strconv.FormatInt(eventsStarted.Load(), 10)
			response["events_closed"] = strconv.FormatInt(eventsClosed.Load(), 10)
			response["ws_started"] = strconv.FormatInt(wsStarted.Load(), 10)
			response["ws_closed"] = strconv.FormatInt(wsClosed.Load(), 10)
		case "api stream counts":
			response["events_started"] = strconv.FormatInt(apiEventsStarted.Load(), 10)
			response["events_closed"] = strconv.FormatInt(apiEventsClosed.Load(), 10)
			response["ws_started"] = strconv.FormatInt(apiWSStarted.Load(), 10)
			response["ws_closed"] = strconv.FormatInt(apiWSClosed.Load(), 10)
		default:
			response["error"] = "unknown fixture command"
		}
		if err := json.NewEncoder(os.Stdout).Encode(response); err != nil {
			return
		}
	}
}

// runtimeNativeAPIRequest verifies the header surface guaranteed by the API
// origin proxy: it keeps only API protocol headers, then installs exactly one
// connector-owned native bearer. Local and Connect authority material must not
// reach the fixture origin.
func runtimeNativeAPIRequest(r *http.Request) bool {
	if r.Method != http.MethodGet || r.Host != edgeAPIHost || r.Header.Get("Authorization") != "Bearer fixture-native-api-token" || len(r.Header.Values("Authorization")) != 1 {
		return false
	}
	for _, name := range []string{"X-Api-Key", "Cookie", "Origin", "Proxy-Authorization", "X-Anvil-Connect-Resource", "X-Anvil-Connect-Identity", "X-Anvil-Connect-Assertion", "X-Anvil-Connect-Authorization"} {
		if len(r.Header.Values(name)) != 0 {
			return false
		}
	}
	return true
}

// runtimeAutheliaConfig keeps the baseline TOTP selection unchanged. The
// passkey-only profile supplies the pinned Authelia WebAuthn policy used by
// P03; users still select the passkey flow explicitly in the browser.
func runtimeAutheliaConfig(authListen, state, users, clientSecret, sessionSecret, storageKey, validationSecret, hmacSecret, rsaKey string, passkey bool) string {
	configuration := edgeConfig(authListen, state, users, clientSecret, sessionSecret, storageKey, validationSecret, hmacSecret, rsaKey)
	if !passkey {
		return configuration
	}
	return configuration + "webauthn:\n  disable: false\n  enable_passkey_login: true\n  experimental_enable_passkey_uv_two_factors: true\n  timeout: '5 seconds'\n  selection_criteria:\n    discoverability: required\n    user_verification: required\n"
}

var runtimeElevationCodePattern = regexp.MustCompile(`(?ms)^A ONE-TIME CODE HAS BEEN GENERATED TO COMPLETE A REQUESTED ACTION\r?\n.*?^----------------------------------------\r?\n\r?\n([ABCDEFGHJKLMNPQRTUVWYXZ2346789]{8})\r?\n\r?\n----------------------------------------(?:\r?\n|$)`)

// runtimeFixtureElevationCode is intentionally closed over the fixture's
// filesystem notifier. It accepts only the pinned IdentityVerificationOTC
// title-and-delimiter form and returns its eight-character code, never a
// notifier path, recipient, or URL.
func runtimeFixtureElevationCode(path string) (string, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&077 != 0 || info.Size() < 1 || info.Size() > 32*1024 {
		return "", os.ErrNotExist
	}
	contents, err := os.ReadFile(path)
	if err != nil || len(contents) != int(info.Size()) {
		return "", os.ErrNotExist
	}
	matches := runtimeElevationCodePattern.FindAllStringSubmatch(string(contents), -1)
	if len(matches) == 0 {
		return "", os.ErrNotExist
	}
	return matches[len(matches)-1][1], nil
}

func runtimeFixtureHumanID(issuer, subject string) string {
	if issuer == "" || subject == "" {
		return ""
	}
	digest := sha256.Sum256([]byte(issuer + "\x00" + subject))
	return "human:" + hex.EncodeToString(digest[:])
}

// runtimeFixtureClient writes only a closed declaration and a sibling local
// key. The Playwright lane gives this private HOME exclusively to the actual
// CLI; no remote credential or approval code is written to disk.
func runtimeFixtureClient(t *testing.T, directory string) (string, string) {
	t.Helper()
	home := filepath.Join(directory, "device-client-home")
	configDir := filepath.Join(home, ".config", "anvil-connect")
	if err := os.MkdirAll(configDir, 0700); err != nil {
		t.Fatal(err)
	}
	localKey, err := client.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	declaration := clientconfig.Config{Schema: "anvil-connect.client-runtime/v1", Rule: config.Rule{ID: "router", Host: edgeAPIHost, PathPrefix: "/v1", Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer", Limits: config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: 5, DurationSeconds: 20}}, Listen: "127.0.0.1:8787", LocalKeyEnv: "ANVIL_CONNECT_LOCAL_KEY", RemoteKeyEnv: "ANVIL_CONNECT_REMOTE_KEY", DeviceAuthorization: &clientconfig.DeviceAuthorization{BrowserHost: dashHost, ApprovalPath: "/_anvil-connect/device", APIResource: "router", Methods: []string{"GET"}}}
	encoded, err := json.Marshal(declaration)
	if err != nil {
		t.Fatal(err)
	}
	edgeWrite(t, filepath.Join(configDir, "client.json"), string(encoded))
	localKeyPath := filepath.Join(configDir, "local-key")
	edgeWrite(t, localKeyPath, localKey+"\n")
	return home, localKeyPath
}

func runtimeWebSocketKey(t *testing.T) string {
	t.Helper()
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		t.Fatal(err)
	}
	return base64.StdEncoding.EncodeToString(nonce)
}

func jsonString(value string) string {
	encoded, _ := json.Marshal(value)
	return string(encoded)
}

func TestRuntimeAutheliaPasskeyProfile(t *testing.T) {
	base := runtimeAutheliaConfig("127.0.0.1:1234", t.TempDir(), "users.yml", "client", "session", "storage", "validation", "hmac", "rsa", false)
	if strings.Contains(base, "webauthn:") || strings.Contains(base, "default_2fa_method") {
		t.Fatal("baseline fixture changed its TOTP-first selection")
	}
	passkey := runtimeAutheliaConfig("127.0.0.1:1234", t.TempDir(), "users.yml", "client", "session", "storage", "validation", "hmac", "rsa", true)
	for _, field := range []string{"enable_passkey_login: true", "experimental_enable_passkey_uv_two_factors: true", "discoverability: required", "user_verification: required", "timeout: '5 seconds'"} {
		if !strings.Contains(passkey, field) {
			t.Fatalf("passkey fixture missing %q", field)
		}
	}
	if strings.Contains(passkey, "default_2fa_method") {
		t.Fatal("passkey fixture must not force a WebAuthn bootstrap login")
	}
}

func TestRuntimeFixtureElevationCodeIsNewestAndClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "notifications.txt")
	contents := "To: fixture-allowed@example.test\n\nA ONE-TIME CODE HAS BEEN GENERATED TO COMPLETE A REQUESTED ACTION\n\nHi Fixture Allowed,\n\n----------------------------------------\n\nWXYZ6789\n\n----------------------------------------\n"
	if err := os.WriteFile(path, []byte(contents), 0600); err != nil {
		t.Fatal(err)
	}
	code, err := runtimeFixtureElevationCode(path)
	if err != nil || code != "WXYZ6789" {
		t.Fatal("newest fixture elevation code was not selected")
	}
	if err := os.WriteFile(path, []byte("A ONE-TIME CODE HAS BEEN GENERATED TO COMPLETE A REQUESTED ACTION\n\n----------------------------------------\n\nSECRETS1\n\n----------------------------------------"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := runtimeFixtureElevationCode(path); err == nil {
		t.Fatal("unlabeled notifier content was accepted")
	}
}

func TestRuntimeCaddyConfigRoutesAPIUpgradeBeforeH2C(t *testing.T) {
	declaration := runtimeCaddyConfig(":443", "127.0.0.1:9091", "/tmp/ingress.sock", "certificate", "key")
	apps := declaration["apps"].(map[string]any)
	httpApp := apps["http"].(map[string]any)
	servers := httpApp["servers"].(map[string]any)
	server := servers["anvil_connect"].(map[string]any)
	routes := server["routes"].([]any)

	upgradeIndex, h2cIndex := -1, -1
	for index, entry := range routes {
		route := entry.(map[string]any)
		matchers, ok := route["match"].([]any)
		if !ok || len(matchers) != 1 {
			continue
		}
		match := matchers[0].(map[string]any)
		hosts, ok := match["host"].([]string)
		if !ok || len(hosts) != 1 || hosts[0] != edgeAPIHost {
			continue
		}
		versions := route["handle"].([]any)[1].(map[string]any)["transport"].(map[string]any)["versions"].([]string)
		switch {
		case match["header_regexp"] != nil && len(versions) == 1 && versions[0] == "1.1":
			upgradeIndex = index
		case match["method"] != nil && len(versions) == 1 && versions[0] == "h2c":
			h2cIndex = index
		}
	}
	if upgradeIndex < 0 || h2cIndex < 0 || upgradeIndex >= h2cIndex {
		t.Fatal("API upgrade route does not precede the API h2c route")
	}
}

func TestRuntimeNativeAPIRequestClosedHeaders(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "http://"+edgeAPIHost+"/v1/events", nil)
	request.Host = edgeAPIHost
	request.Header.Set("Authorization", "Bearer fixture-native-api-token")
	if !runtimeNativeAPIRequest(request) {
		t.Fatal("native API request rejected")
	}
	for _, name := range []string{"X-Api-Key", "Cookie", "Origin", "Proxy-Authorization", "X-Anvil-Connect-Resource", "X-Anvil-Connect-Identity", "X-Anvil-Connect-Assertion", "X-Anvil-Connect-Authorization"} {
		candidate := request.Clone(context.Background())
		candidate.Header.Set(name, "fixture-leak")
		if runtimeNativeAPIRequest(candidate) {
			t.Fatalf("leaked header %q reached native API", name)
		}
	}
	request.Header.Add("Authorization", "Bearer fixture-native-api-token")
	if runtimeNativeAPIRequest(request) {
		t.Fatal("duplicate native bearer accepted")
	}
	request.Header.Del("Authorization")
	request.Header.Set("Authorization", "Bearer fixture-native-api-token")
	request.Host = "wrong.example.test"
	if runtimeNativeAPIRequest(request) {
		t.Fatal("wrong native API host accepted")
	}
}
