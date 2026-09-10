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
	edgeControlHost  = "control.example.test"
	edgeTunnelHost   = "tunnel.example.test"
	edgeAPIHost      = "api.example.test"
	edgeRetainedHost = "retain.example.test"
)

const (
	runtimeFixtureDefaultIdleSeconds     = 5
	runtimeFixtureDefaultDurationSeconds = 20
	runtimeFixtureExpiryIdleSeconds      = 150
	runtimeFixtureExpiryDurationSeconds  = 180
	runtimeFixtureExpirySessionSeconds   = 120
	runtimeFixtureRestartConcurrent      = 3
	runtimeFixtureRestartMaxConcurrent   = 5
)

type runtimeFixtureProfile struct {
	device                        bool
	passkey                       bool
	expiry                        bool
	restart                       bool
	grant                         bool
	browserSessionLifetimeSeconds int
	limits                        config.Limits
	apiLimits                     config.Limits
	gatewayMaxConcurrent          int
}

func newRuntimeFixtureProfile(device, passkey, expiry, restart, grant bool) (runtimeFixtureProfile, bool) {
	if (expiry && passkey) || (restart && (passkey || expiry)) || (grant && (passkey || expiry || restart)) {
		return runtimeFixtureProfile{}, false
	}
	profile := runtimeFixtureProfile{
		device:               device || passkey || expiry || restart || grant,
		passkey:              passkey,
		expiry:               expiry,
		restart:              restart,
		grant:                grant,
		limits:               config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: runtimeFixtureDefaultIdleSeconds, DurationSeconds: runtimeFixtureDefaultDurationSeconds},
		apiLimits:            config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: runtimeFixtureDefaultIdleSeconds, DurationSeconds: runtimeFixtureDefaultDurationSeconds},
		gatewayMaxConcurrent: 4,
	}
	if expiry {
		profile.browserSessionLifetimeSeconds = runtimeFixtureExpirySessionSeconds
		profile.limits.IdleSeconds = runtimeFixtureExpiryIdleSeconds
		profile.limits.DurationSeconds = runtimeFixtureExpiryDurationSeconds
		profile.apiLimits.IdleSeconds = runtimeFixtureExpiryIdleSeconds
		profile.apiLimits.DurationSeconds = runtimeFixtureExpiryDurationSeconds
	}
	if restart {
		// The restart lane holds browser SSE, WebSocket, and a blocked POST
		// concurrently. Its larger bounded limits are isolated from ordinary
		// baseline, device, passkey, and expiry profiles.
		profile.limits.Concurrent = runtimeFixtureRestartConcurrent
		profile.limits.IdleSeconds = runtimeFixtureExpiryIdleSeconds
		profile.limits.DurationSeconds = runtimeFixtureExpiryDurationSeconds
		profile.apiLimits.IdleSeconds = runtimeFixtureExpiryIdleSeconds
		profile.apiLimits.DurationSeconds = runtimeFixtureExpiryDurationSeconds
		profile.gatewayMaxConcurrent = runtimeFixtureRestartMaxConcurrent
	}
	return profile, true
}

func runtimeFixtureBrowserAdministration(profile runtimeFixtureProfile, deviceHuman, passkeyOperator string) (*config.BrowserAdministration, bool) {
	switch {
	case profile.passkey:
		if passkeyOperator == "" || passkeyOperator == deviceHuman {
			return nil, false
		}
		return &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{passkeyOperator}}, true
	case profile.expiry:
		if deviceHuman == "" {
			return nil, false
		}
		// The expiry lane provisions this designated operator only through the
		// ordinary fixture grant command after browser admission.
		return &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{deviceHuman}}, true
	default:
		return nil, true
	}
}

// runtimeCaddyConfig is the same public split used by the managed renderer:
// the tunnel and resource upgrades take HTTP/1.1, while ordinary browser, API,
// and control requests use h2c over the gateway's same-UID ingress socket.
func runtimeCaddyConfig(listen, authListen, socket, certificate, key string, browserHosts []string) map[string]any {
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
	browserUpgrade := map[string]any{"host": browserHosts, "path": []string{"/", "/*"}, "header_regexp": upgrade}
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
					route(map[string]any{"host": browserHosts, "path": []string{"/", "/*"}, "method": []string{"GET", "POST"}}, []string{"h2c"}),
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

func (o *runtimeProxyObserver) count(host string) int {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.counts[host]
}

func runtimeCaddyControlReady(caddyListen string, roots *x509.CertPool) bool {
	transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots.Clone(), ServerName: edgeControlHost}, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != edgeControlHost+":443" {
			return nil, &net.AddrError{Err: "fixture control destination denied", Addr: address}
		}
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", caddyListen)
	}}
	defer transport.CloseIdleConnections()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		requestContext, cancel := context.WithDeadline(context.Background(), deadline)
		request, requestErr := http.NewRequestWithContext(requestContext, http.MethodGet, "https://"+edgeControlHost+"/v1/challenge", nil)
		if requestErr != nil {
			cancel()
			return false
		}
		response, err := (&http.Client{Transport: transport, Timeout: 2 * time.Second}).Do(request)
		if err == nil && response != nil {
			io.Copy(io.Discard, response.Body)
			response.Body.Close()
			cancel()
			// A GET on the control authority is deliberately malformed. A 400
			// proves Caddy reached the restarted Connect control handler.
			if response.StatusCode == http.StatusBadRequest {
				return true
			}
		} else if response != nil {
			response.Body.Close()
		}
		cancel()
		time.Sleep(50 * time.Millisecond)
	}
	return false
}

func runtimeCaddyIssuerReady(caddyListen string, roots *x509.CertPool) bool {
	if caddyListen == "" || roots == nil {
		return false
	}
	transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots.Clone(), ServerName: edgeAuthHost}, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != edgeAuthHost+":443" {
			return nil, &net.AddrError{Err: "fixture issuer destination denied", Addr: address}
		}
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", caddyListen)
	}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 2 * time.Second}
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		ctx, cancel := context.WithDeadline(context.Background(), deadline)
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://"+edgeAuthHost+"/api/health", nil)
		if err != nil {
			cancel()
			return false
		}
		response, err := client.Do(request)
		if response != nil {
			io.Copy(io.Discard, response.Body)
			response.Body.Close()
		}
		cancel()
		if err == nil && response != nil && response.StatusCode == http.StatusOK {
			return true
		}
		time.Sleep(100 * time.Millisecond)
	}
	return false
}

func runtimeConnectorReady(connector *connectruntime.Connector, observer *runtimeProxyObserver, priorTunnels int, resources []config.Resource) bool {
	if len(resources) == 0 {
		return false
	}
	deadline := time.Now().Add(10 * time.Second)
	for observer.count(edgeTunnelHost+":443") <= priorTunnels && time.Now().Before(deadline) {
		select {
		case <-connector.Done():
			return false
		default:
		}
		time.Sleep(50 * time.Millisecond)
	}
	if observer.count(edgeTunnelHost+":443") <= priorTunnels {
		return false
	}
	// Every declared reverse listener must belong to the new running stack;
	// readiness of the dashboard alone does not establish API readiness.
	for _, resource := range resources {
		ready := false
		for time.Now().Before(deadline) {
			select {
			case <-connector.Done():
				return false
			default:
			}
			connection, err := net.DialTimeout("tcp4", resource.TunnelAddress, 200*time.Millisecond)
			if err == nil {
				connection.Close()
				ready = true
				break
			}
			time.Sleep(50 * time.Millisecond)
		}
		if !ready {
			return false
		}
	}
	return true
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
	profile, validProfile := newRuntimeFixtureProfile(
		os.Getenv("ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE") == "1",
		os.Getenv("ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE") == "1",
		os.Getenv("ANVIL_CONNECT_BROWSER_EXPIRY_FIXTURE") == "1",
		os.Getenv("ANVIL_CONNECT_BROWSER_RESTART_FIXTURE") == "1",
		os.Getenv("ANVIL_CONNECT_BROWSER_GRANT_FIXTURE") == "1",
	)
	if !validProfile {
		t.Fatal("incompatible browser fixture profile")
	}
	passkeyFixture, deviceFixture, expiryFixture, restartFixture, grantFixture := profile.passkey, profile.device, profile.expiry, profile.restart, profile.grant
	lifecycleFixture := restartFixture || grantFixture
	if os.Getenv("ANVIL_CONNECT_BROWSER_RUNTIME_EDGE_FIXTURE") != "1" && !deviceFixture {
		t.Skip("launched only by the runtime-edge Playwright test")
	}
	caddy, authelia := edgeTool(t, "caddy"), edgeTool(t, "authelia")
	binary := runtimeTunnelBinary(t)
	directory := t.TempDir()
	// Restore requires an owner-private destination parent. TempDir's final
	// child follows the process umask, so make this fixture boundary explicit.
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal("fixture private directory setup failed")
	}
	secrets, state, childHome := filepath.Join(directory, "secrets"), filepath.Join(directory, "state"), filepath.Join(directory, "child-home")
	for _, path := range []string{secrets, state, childHome} {
		if err := os.Mkdir(path, 0700); err != nil {
			t.Fatal(err)
		}
	}
	certificateNames := []string{edgeAuthHost, dashHost, edgeAPIHost, edgeControlHost, edgeTunnelHost}
	if grantFixture {
		certificateNames = append(certificateNames, edgeRetainedHost)
	}
	certificate, key, root, roots := edgeCertificate(t, certificateNames...)
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
	edgeWrite(t, authConfig, runtimeAutheliaConfig(authListen, state, users, clientSecretPath, secretPaths["session"], secretPaths["storage"], secretPaths["validation"], secretPaths["hmac"], oidcPath, passkeyFixture, grantFixture))
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

	resources := []config.Resource{{Rule: config.Rule{ID: "dash", Host: dashHost, PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none", Limits: profile.limits}, Connector: "connector-a", TunnelAddress: edgeReserve(t)}}
	if grantFixture {
		resources = append(resources, config.Resource{Rule: config.Rule{ID: "retain", Host: edgeRetainedHost, PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none", Limits: profile.limits}, Connector: "connector-a", TunnelAddress: edgeReserve(t)})
	}
	if deviceFixture {
		resources = append(resources, config.Resource{Rule: config.Rule{ID: "router", Host: edgeAPIHost, PathPrefix: "/v1", Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer", Limits: profile.apiLimits}, Connector: "connector-a", TunnelAddress: edgeReserve(t)})
	}
	gatewayDeclaration := config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: edgeReserve(t), MaxConcurrent: profile.gatewayMaxConcurrent, Resources: resources}
	operatorHuman := ""
	if deviceFixture {
		gatewayDeclaration.DeviceAuthorizations = []config.DeviceAuthorization{{BrowserResource: "dash", APIResource: "router", Methods: []string{"GET"}, Label: "Fixture terminal", Principals: map[string]string{deviceHuman: "fixture-sdk"}}}
	}
	if passkeyFixture {
		operatorHuman = runtimeFixtureHumanID("https://"+edgeAuthHost, "fixture-operator")
	}
	administration, administrationOK := runtimeFixtureBrowserAdministration(profile, deviceHuman, operatorHuman)
	if !administrationOK {
		t.Fatal("fixture browser administrator is unavailable")
	}
	if administration != nil {
		gatewayDeclaration.BrowserAdministration = administration
	}
	gatewayCfg := connectruntime.GatewayConfig{Schema: "anvil-connect.gateway-runtime/v1", Gateway: gatewayDeclaration, ControlHost: edgeControlHost, TunnelHost: edgeTunnelHost, StateDirectory: filepath.Join(directory, "gateway"), TunnelBinary: binary, TunnelListen: edgeReserve(t), OIDC: connectruntime.OIDC{Issuer: "https://" + edgeAuthHost, ClientID: "connect-browser", ClientSecretEnv: "OIDC_CLIENT_SECRET"}}
	if expiryFixture {
		lifetime := profile.browserSessionLifetimeSeconds
		gatewayCfg.BrowserSessionLifetimeSeconds = &lifetime
	}
	browserHosts := []string{dashHost}
	if grantFixture {
		browserHosts = append(browserHosts, edgeRetainedHost)
	}
	caddyConfigPath := filepath.Join(directory, "caddy.json")
	startCaddy := func(stateDirectory string) *edgeChild {
		caddyConfig, marshalErr := json.Marshal(runtimeCaddyConfig(caddyListen, authListen, filepath.Join(stateDirectory, "ingress.sock"), certificatePath, keyPath, browserHosts))
		if marshalErr != nil {
			t.Fatal(marshalErr)
		}
		edgeWrite(t, caddyConfigPath, string(caddyConfig))
		return startEdgeChild(t, childHome, caddy, "run", "--config", caddyConfigPath)
	}
	caddyChild := startCaddy(gatewayCfg.StateDirectory)

	if !runtimeCaddyIssuerReady(caddyListen, roots) {
		t.Fatal("pinned Authelia and Caddy edge did not become healthy")
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
	gatewaySecrets := func(name string) (string, bool) { return clientSecret, name == "OIDC_CLIENT_SECRET" }
	gatewayContext, gatewayCancel := context.WithCancel(context.Background())
	gateway, err := connectruntime.StartGateway(gatewayContext, gatewayCfg, gatewaySecrets)
	if err != nil {
		gatewayCancel()
		t.Fatal(err)
	}
	defer func() {
		gatewayCancel()
		if gateway != nil {
			gateway.Close()
		}
	}()
	adminCall := func(request admin.Request) (admin.Response, error) {
		gatewayDirectory, openErr := privatefiles.Open(gatewayCfg.StateDirectory)
		if openErr != nil {
			return admin.Response{}, openErr
		}
		defer gatewayDirectory.Close()
		adminPin, pinErr := gatewayDirectory.PinPath("admin.sock")
		if pinErr != nil {
			return admin.Response{}, pinErr
		}
		defer adminPin.Close()
		return admin.Call(context.Background(), adminPin.Path(), request)
	}

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

	if deviceFixture {
		if _, err := adminCall(admin.Request{Operation: "principal-set", Principal: "fixture-sdk", Grants: []access.Grant{{Resource: "router", Methods: []string{"GET"}}}}); err != nil {
			t.Fatal("fixture API principal setup failed")
		}
	}
	if passkeyFixture {
		operator, operatorErr := adminCall(admin.Request{Operation: "human-set", Issuer: "https://" + edgeAuthHost, Subject: "fixture-operator", Resources: []string{"dash"}})
		if operatorErr != nil || operator.Principal != operatorHuman || operator.Principal == deviceHuman {
			t.Fatal("fixture browser administrator setup failed")
		}
	}
	nativeFixture := &edgeFixture{}
	var eventsStarted, eventsClosed, wsStarted, wsClosed atomic.Int64
	var restartPostStarted, restartPostClosed atomic.Int64
	native := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/restart-blocked-post" {
			if !restartFixture || r.Method != http.MethodPost {
				http.NotFound(w, r)
				return
			}
			restartPostStarted.Add(1)
			defer restartPostClosed.Add(1)
			// The restart probe has no native mutation. It remains held until
			// Connect cancels this request's context during gateway shutdown.
			<-r.Context().Done()
			return
		}
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
	var retainedNative *httptest.Server
	if grantFixture {
		retainedFixture := &edgeFixture{}
		retainedNative = httptest.NewServer(http.HandlerFunc(retainedFixture.nativeDashboard))
		defer retainedNative.Close()
	}
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
	if grantFixture {
		retainedResource := gatewayCfg.Gateway.Resources[1]
		connectorResources = append(connectorResources, connectruntime.ConnectorResource{Envelope: config.Envelope{Rule: retainedResource.Rule, Listen: edgeReserve(t), OriginURL: retainedNative.URL}, ReverseAddress: retainedResource.TunnelAddress})
		inviteResources = append(inviteResources, "retain")
	}
	if deviceFixture {
		apiResource := gatewayCfg.Gateway.Resources[len(gatewayCfg.Gateway.Resources)-1]
		connectorResources = append(connectorResources, connectruntime.ConnectorResource{Envelope: config.Envelope{Rule: apiResource.Rule, Listen: edgeReserve(t), OriginURL: api.URL, TokenEnv: "ANVIL_CONNECT_FIXTURE_API_TOKEN"}, ReverseAddress: apiResource.TunnelAddress})
		inviteResources = append(inviteResources, "router")
	}
	connectorCfg := connectruntime.ConnectorConfig{Schema: "anvil-connect.connector-runtime/v1", ID: "connector-a", ControlHost: edgeControlHost, TunnelHost: edgeTunnelHost, StateDirectory: filepath.Join(directory, "connector"), TunnelBinary: binary, PublicTrustFile: rootPath, HTTPProxyURL: proxyServer.URL, Resources: connectorResources}
	invite, err := adminCall(admin.Request{Operation: "invite", Installation: "connector-a", Role: "connector", Resources: inviteResources, LifetimeSeconds: 60})
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
	if _, err := adminCall(admin.Request{Operation: "approve", Installation: "connector-a", Fingerprint: identity.Fingerprint}); err != nil {
		t.Fatal(err)
	}
	connectorSecrets := func(name string) (string, bool) {
		return "fixture-native-api-token", deviceFixture && name == "ANVIL_CONNECT_FIXTURE_API_TOKEN"
	}
	connector, err := connectruntime.StartConnector(context.Background(), connectorCfg, connectorSecrets)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if connector != nil {
			connector.Close()
		}
	}()
	// StartConnector reports process ownership, not reverse-tunnel readiness.
	if !runtimeConnectorReady(connector, proxyObserver, 0, gatewayCfg.Gateway.Resources) {
		t.Fatal("connector did not become ready")
	}

	ready := map[string]string{"url": "https://" + dashHost, "resolver": caddyListen, "ca": rootPath, "allowed_user": "fixture-allowed", "allowed_password": allowedPassword}
	if deviceFixture {
		clientHome, localKeyPath := runtimeFixtureClient(t, directory, profile.apiLimits)
		ready["client_home"] = clientHome
		ready["local_key"] = localKeyPath
		ready["local_base_url"] = "http://127.0.0.1:8787/v1"
		if restartFixture {
			secondaryListen := edgeReserve(t)
			secondaryHome, secondaryKeyPath := runtimeFixtureClientAt(t, directory, "device-client-secondary-home", profile.apiLimits, secondaryListen)
			ready["client_home_secondary"] = secondaryHome
			ready["local_key_secondary"] = secondaryKeyPath
			ready["local_base_url_secondary"] = "http://" + secondaryListen + "/v1"
		}
	}
	if passkeyFixture {
		ready["passkey_fixture"] = "enabled"
	}
	if expiryFixture {
		ready["session_lifetime_seconds"] = strconv.Itoa(profile.browserSessionLifetimeSeconds)
	}
	if restartFixture {
		ready["restart_fixture"] = "enabled"
		ready["restart_post_path"] = "/restart-blocked-post"
	}
	if grantFixture {
		ready["grant_fixture"] = "enabled"
		ready["retained_url"] = "https://" + edgeRetainedHost
	}
	if err := json.NewEncoder(os.Stdout).Encode(ready); err != nil {
		t.Fatal(err)
	}
	commands := fixtureCommands(os.Stdin)
	restartEpoch := ""
	resetPending := false
	restorePending := false
	restoreApproved := false
	for received := range commands {
		if received.err != nil {
			t.Error(errFixtureCommandInput)
			return
		}
		command := received.command
		response := map[string]string{"ack": command}
		switch command {
		case "reset authority":
			if !restartFixture || gateway == nil || connector == nil || restartEpoch != "" || resetPending || restorePending {
				response["error"] = "reset fixture is unavailable"
				break
			}
			before, statusErr := adminCall(admin.Request{Operation: "status"})
			if statusErr != nil || len(before.Epoch) != 64 {
				response["error"] = "reset status is unavailable"
				break
			}
			reset, resetErr := adminCall(admin.Request{Operation: "authority-reset"})
			after, afterErr := adminCall(admin.Request{Operation: "status"})
			if resetErr != nil || afterErr != nil || len(reset.Epoch) != 64 || len(after.Epoch) != 64 || reset.Epoch != after.Epoch || before.Epoch == after.Epoch {
				response["error"] = "authority reset failed"
				break
			}
			// The old connector remains live until the browser harness observes
			// cancellation through its existing streams and native handlers.
			resetPending = true
			response["epoch_changed"] = "true"
		case "reenroll connector":
			// The ack correlates both success and failure; the command client
			// rejects any error before accepting a successful replacement.
			if !restartFixture || !resetPending || gateway == nil || connector == nil || restartEpoch != "" {
				response["error"] = "connector reenrollment is unavailable"
				break
			}
			nextConnectorCfg := connectorCfg
			nextConnectorCfg.StateDirectory = filepath.Join(directory, "connector-reset")
			if _, stateErr := os.Lstat(nextConnectorCfg.StateDirectory); !os.IsNotExist(stateErr) {
				response["error"] = "connector reenrollment state is unavailable"
				break
			}
			priorTunnels := proxyObserver.count(edgeTunnelHost + ":443")
			connector.Close()
			connector = nil
			invite, inviteErr := adminCall(admin.Request{Operation: "invite", Installation: nextConnectorCfg.ID, Role: "connector", Resources: inviteResources, LifetimeSeconds: 60})
			if inviteErr != nil {
				response["error"] = "connector reenrollment invitation failed"
				break
			}
			if initErr := connectruntime.InitializeConnector(context.Background(), nextConnectorCfg, invite); initErr != nil {
				response["error"] = "connector reenrollment initialization failed"
				break
			}
			identity, identityErr := connectruntime.ConnectorIdentity(nextConnectorCfg)
			if identityErr != nil {
				response["error"] = "connector reenrollment identity failed"
				break
			}
			if _, approveErr := adminCall(admin.Request{Operation: "approve", Installation: nextConnectorCfg.ID, Fingerprint: identity.Fingerprint}); approveErr != nil {
				response["error"] = "connector reenrollment approval failed"
				break
			}
			nextConnector, startErr := connectruntime.StartConnector(context.Background(), nextConnectorCfg, connectorSecrets)
			if startErr != nil {
				response["error"] = "connector reenrollment start failed"
				break
			}
			if !runtimeConnectorReady(nextConnector, proxyObserver, priorTunnels, gatewayCfg.Gateway.Resources) {
				nextConnector.Close()
				response["error"] = "connector reenrollment readiness failed"
				break
			}
			connectorCfg = nextConnectorCfg
			connector = nextConnector
			resetPending = false
			response["ack"] = "reenroll connector"
		case "stop runtime":
			if !lifecycleFixture || gateway == nil || connector == nil || restartEpoch != "" || resetPending || restorePending {
				response["error"] = "restart fixture is disabled"
				break
			}
			before, statusErr := adminCall(admin.Request{Operation: "status"})
			if statusErr != nil || len(before.Epoch) != 64 {
				response["error"] = "restart status is unavailable"
				break
			}
			// Initiate the same parent-context cancellation used by native
			// service shutdown. Keep full process draining out of the request
			// cancellation measurement; Close may include graceful server waits.
			restartEpoch = before.Epoch
			gatewayCancel()
		case "backup restore authority":
			failRestore := func(message string) {
				response["error"] = message
				if _, _, line, ok := stdruntime.Caller(1); ok {
					response["error_location"] = "browser_runtime_fixture_test.go:" + strconv.Itoa(line)
				}
			}
			if !restartFixture || gateway == nil || connector == nil || len(restartEpoch) != 64 || resetPending || restorePending {
				failRestore("authority restore is unavailable")
				break
			}
			// The caller observed closure after stop runtime. Join the old stack
			// before BackupGateway takes its exclusive authority-database lock.
			gateway.Close()
			gateway = nil
			connector.Close()
			connector = nil
			backup, backupErr := connectruntime.BackupGateway(gatewayCfg)
			if backupErr != nil {
				failRestore("authority backup failed")
				break
			}
			restoredCfg := gatewayCfg
			restoredCfg.StateDirectory = filepath.Join(directory, "gateway-restore")
			if _, stateErr := os.Lstat(restoredCfg.StateDirectory); !os.IsNotExist(stateErr) {
				failRestore("authority restore state is unavailable")
				break
			}
			if restoreErr := connectruntime.RestoreGateway(restoredCfg, backup, connectruntime.BackupDigest(backup)); restoreErr != nil {
				failRestore("authority restore failed")
				break
			}
			if closeErr := caddyChild.Close(); closeErr != nil {
				failRestore("authority restore edge shutdown failed")
				break
			}
			caddyChild = startCaddy(restoredCfg.StateDirectory)
			if !runtimeCaddyIssuerReady(caddyListen, roots) {
				_ = caddyChild.Close()
				failRestore("authority restore edge readiness failed")
				break
			}
			restoredContext, restoredCancel := context.WithCancel(context.Background())
			restoredGateway, startErr := connectruntime.StartGateway(restoredContext, restoredCfg, gatewaySecrets)
			if startErr != nil {
				restoredCancel()
				_ = caddyChild.Close()
				failRestore("authority restore gateway start failed")
				break
			}
			gateway, gatewayCancel, gatewayCfg = restoredGateway, restoredCancel, restoredCfg
			if !runtimeCaddyControlReady(caddyListen, roots) {
				gateway.Close()
				gateway = nil
				_ = caddyChild.Close()
				failRestore("authority restore readiness failed")
				break
			}
			after, statusErr := adminCall(admin.Request{Operation: "status"})
			if statusErr != nil || len(after.Epoch) != 64 || after.Epoch == restartEpoch {
				gateway.Close()
				gateway = nil
				_ = caddyChild.Close()
				failRestore("authority restore epoch failed")
				break
			}
			restartEpoch = ""
			restorePending = true
			response["epoch_changed"] = "true"
		case "reapprove restored access":
			if !restartFixture || !restorePending || restoreApproved || gateway == nil || connector != nil || restartEpoch != "" {
				response["error"] = "restored access approval is unavailable"
				break
			}
			if _, grantErr := adminCall(admin.Request{Operation: "human-set", Issuer: "https://" + edgeAuthHost, Subject: allowedSubject, Resources: []string{"dash"}, Disabled: false}); grantErr != nil {
				response["error"] = "restored human approval failed"
				break
			}
			if _, grantErr := adminCall(admin.Request{Operation: "principal-set", Principal: "fixture-sdk", Grants: []access.Grant{{Resource: "router", Methods: []string{"GET"}}}, Disabled: false}); grantErr != nil {
				_, _ = adminCall(admin.Request{Operation: "human-set", Issuer: "https://" + edgeAuthHost, Subject: allowedSubject, Resources: []string{"dash"}, Disabled: true})
				response["error"] = "restored API approval failed"
				break
			}
			restoreApproved = true
		case "reenroll restored connector":
			if !restartFixture || !restorePending || !restoreApproved || gateway == nil || connector != nil || restartEpoch != "" {
				response["error"] = "restored connector enrollment is unavailable"
				break
			}
			nextConnectorCfg := connectorCfg
			nextConnectorCfg.StateDirectory = filepath.Join(directory, "connector-restore")
			if _, stateErr := os.Lstat(nextConnectorCfg.StateDirectory); !os.IsNotExist(stateErr) {
				response["error"] = "restored connector state is unavailable"
				break
			}
			priorTunnels := proxyObserver.count(edgeTunnelHost + ":443")
			invite, inviteErr := adminCall(admin.Request{Operation: "invite", Installation: nextConnectorCfg.ID, Role: "connector", Resources: inviteResources, LifetimeSeconds: 60})
			if inviteErr != nil {
				response["error"] = "restored connector invitation failed"
				break
			}
			if initErr := connectruntime.InitializeConnector(context.Background(), nextConnectorCfg, invite); initErr != nil {
				response["error"] = "restored connector initialization failed"
				break
			}
			identity, identityErr := connectruntime.ConnectorIdentity(nextConnectorCfg)
			if identityErr != nil {
				response["error"] = "restored connector identity failed"
				break
			}
			if _, approveErr := adminCall(admin.Request{Operation: "approve", Installation: nextConnectorCfg.ID, Fingerprint: identity.Fingerprint}); approveErr != nil {
				response["error"] = "restored connector approval failed"
				break
			}
			nextConnector, startErr := connectruntime.StartConnector(context.Background(), nextConnectorCfg, connectorSecrets)
			if startErr != nil {
				response["error"] = "restored connector start failed"
				break
			}
			if !runtimeConnectorReady(nextConnector, proxyObserver, priorTunnels, gatewayCfg.Gateway.Resources) {
				nextConnector.Close()
				response["error"] = "restored connector readiness failed"
				break
			}
			connectorCfg = nextConnectorCfg
			connector = nextConnector
			restorePending = false
			restoreApproved = false
			response["ack"] = "reenroll restored connector"
		case "start runtime":
			if !lifecycleFixture || gateway == nil || connector == nil || len(restartEpoch) != 64 || resetPending {
				response["error"] = "restart fixture is unavailable"
				break
			}
			// The caller has already observed client and native closure.
			// Join the complete old stack before replacing any owned listener.
			gateway.Close()
			gateway = nil
			connector.Close()
			connector = nil
			priorTunnels := proxyObserver.count(edgeTunnelHost + ":443")
			restartedContext, restartedCancel := context.WithCancel(context.Background())
			restartedGateway, startErr := connectruntime.StartGateway(restartedContext, gatewayCfg, gatewaySecrets)
			if startErr != nil {
				restartedCancel()
				response["error"] = "gateway restart failed"
				break
			}
			gateway, gatewayCancel = restartedGateway, restartedCancel
			if !runtimeCaddyControlReady(caddyListen, roots) {
				gateway.Close()
				gateway = nil
				response["error"] = "gateway restart readiness failed"
				break
			}
			restartedConnector, connectorErr := connectruntime.StartConnector(context.Background(), connectorCfg, connectorSecrets)
			if connectorErr != nil {
				gateway.Close()
				gateway = nil
				response["error"] = "connector restart failed"
				break
			}
			connector = restartedConnector
			if !runtimeConnectorReady(connector, proxyObserver, priorTunnels, gatewayCfg.Gateway.Resources) {
				connector.Close()
				connector = nil
				gateway.Close()
				gateway = nil
				response["error"] = "connector restart readiness failed"
				break
			}
			after, statusErr := adminCall(admin.Request{Operation: "status"})
			if statusErr != nil || len(after.Epoch) != 64 || after.Epoch != restartEpoch {
				connector.Close()
				connector = nil
				gateway.Close()
				gateway = nil
				response["error"] = "restart epoch changed"
				break
			}
			restartEpoch = ""
			response["epoch_equal"] = "true"
		case "restart post counts":
			if !restartFixture {
				response["error"] = "restart fixture is disabled"
				break
			}
			response["started"] = strconv.FormatInt(restartPostStarted.Load(), 10)
			response["closed"] = strconv.FormatInt(restartPostClosed.Load(), 10)
		case "grant both browser resources":
			if !grantFixture || gateway == nil || connector == nil || restartEpoch != "" || resetPending || restorePending {
				response["error"] = "browser resource grant is unavailable"
				break
			}
			if _, grantErr := adminCall(admin.Request{Operation: "human-set", Issuer: "https://" + edgeAuthHost, Subject: allowedSubject, Resources: []string{"dash", "retain"}, Disabled: false}); grantErr != nil {
				response["error"] = "browser resource grant failed"
			}
		case "retain alternate browser resource":
			if !grantFixture || gateway == nil || connector == nil || restartEpoch != "" || resetPending || restorePending {
				response["error"] = "browser resource removal is unavailable"
				break
			}
			if _, grantErr := adminCall(admin.Request{Operation: "human-set", Issuer: "https://" + edgeAuthHost, Subject: allowedSubject, Resources: []string{"retain"}, Disabled: false}); grantErr != nil {
				response["error"] = "browser resource removal failed"
			}
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
				if _, grantErr := adminCall(admin.Request{Operation: "human-set", Issuer: "https://" + edgeAuthHost, Subject: allowedSubject, Resources: []string{"dash"}, Disabled: command == "disable allowed"}); grantErr != nil {
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
			parts := strings.Split(command, " ")
			if strings.HasPrefix(command, "api-key-revoke") {
				if len(parts) != 2 || parts[0] != "api-key-revoke" {
					response["error"] = "api key revocation denied"
					break
				}
				keyID := parts[1]
				if !restartFixture || !runtimeCanonicalKeyID(keyID) {
					response["error"] = "api key revocation denied"
				} else if _, revokeErr := adminCall(admin.Request{Operation: "api-key-revoke", KeyID: keyID}); revokeErr != nil {
					response["error"] = "api key revocation denied"
				}
			} else {
				response["error"] = "unknown fixture command"
			}
		}
		if err := json.NewEncoder(os.Stdout).Encode(response); err != nil {
			return
		}
	}
}

func runtimeCanonicalKeyID(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 16 && hex.EncodeToString(decoded) == value
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
func runtimeAutheliaConfig(authListen, state, users, clientSecret, sessionSecret, storageKey, validationSecret, hmacSecret, rsaKey string, passkey, grant bool) string {
	redirects := []string{"https://" + dashHost + "/_anvil-connect/callback"}
	if grant {
		redirects = append(redirects, "https://"+edgeRetainedHost+"/_anvil-connect/callback")
	}
	configuration := edgeConfig(authListen, state, users, clientSecret, sessionSecret, storageKey, validationSecret, hmacSecret, rsaKey, redirects...)
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

func runtimeFixtureClientDeclaration(limits config.Limits) clientconfig.Config {
	return runtimeFixtureClientDeclarationAt(limits, "127.0.0.1:8787")
}

func runtimeFixtureClientDeclarationAt(limits config.Limits, listen string) clientconfig.Config {
	return clientconfig.Config{Schema: "anvil-connect.client-runtime/v1", Rule: config.Rule{ID: "router", Host: edgeAPIHost, PathPrefix: "/v1", Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer", Limits: limits}, Listen: listen, LocalKeyEnv: "ANVIL_CONNECT_LOCAL_KEY", RemoteKeyEnv: "ANVIL_CONNECT_REMOTE_KEY", DeviceAuthorization: &clientconfig.DeviceAuthorization{BrowserHost: dashHost, ApprovalPath: "/_anvil-connect/device", APIResource: "router", Methods: []string{"GET"}}}
}

// runtimeFixtureClient writes only a closed declaration and a sibling local
// key. The Playwright lane gives this private HOME exclusively to the actual
// CLI; no remote credential or approval code is written to disk.
func runtimeFixtureClient(t *testing.T, directory string, limits config.Limits) (string, string) {
	return runtimeFixtureClientAt(t, directory, "device-client-home", limits, "127.0.0.1:8787")
}

func runtimeFixtureClientAt(t *testing.T, directory, homeName string, limits config.Limits, listen string) (string, string) {
	t.Helper()
	home := filepath.Join(directory, homeName)
	configDir := filepath.Join(home, ".config", "anvil-connect")
	if err := os.MkdirAll(configDir, 0700); err != nil {
		t.Fatal(err)
	}
	localKey, err := client.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	declaration := runtimeFixtureClientDeclarationAt(limits, listen)
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

func TestRuntimeFixtureProfilesKeepExpirySeparateAndStreamsLive(t *testing.T) {
	baseline, ok := newRuntimeFixtureProfile(false, false, false, false, false)
	if !ok || baseline.device || baseline.passkey || baseline.expiry || baseline.restart || baseline.grant || baseline.browserSessionLifetimeSeconds != 0 || baseline.limits.IdleSeconds != runtimeFixtureDefaultIdleSeconds || baseline.limits.DurationSeconds != runtimeFixtureDefaultDurationSeconds || baseline.apiLimits != baseline.limits || baseline.gatewayMaxConcurrent != 4 {
		t.Fatal("baseline fixture profile changed")
	}
	device, ok := newRuntimeFixtureProfile(true, false, false, false, false)
	if !ok || !device.device || device.passkey || device.expiry || device.restart || device.grant || device.limits != baseline.limits || device.apiLimits != baseline.apiLimits || device.gatewayMaxConcurrent != baseline.gatewayMaxConcurrent {
		t.Fatal("device fixture profile changed")
	}
	passkey, ok := newRuntimeFixtureProfile(false, true, false, false, false)
	if !ok || !passkey.device || !passkey.passkey || passkey.expiry || passkey.restart || passkey.grant || passkey.limits != baseline.limits || passkey.apiLimits != baseline.apiLimits || passkey.gatewayMaxConcurrent != baseline.gatewayMaxConcurrent {
		t.Fatal("passkey fixture profile changed")
	}
	expiry, ok := newRuntimeFixtureProfile(false, false, true, false, false)
	if !ok || !expiry.device || expiry.passkey || !expiry.expiry || expiry.restart || expiry.grant || expiry.browserSessionLifetimeSeconds != runtimeFixtureExpirySessionSeconds || expiry.limits.IdleSeconds != runtimeFixtureExpiryIdleSeconds || expiry.limits.DurationSeconds != runtimeFixtureExpiryDurationSeconds || expiry.apiLimits != expiry.limits || expiry.gatewayMaxConcurrent != baseline.gatewayMaxConcurrent {
		t.Fatal("expiry fixture profile did not retain its bounded session and stream limits")
	}
	if _, ok := newRuntimeFixtureProfile(false, true, true, false, false); ok {
		t.Fatal("expiry and passkey fixture profiles combined")
	}
	restart, ok := newRuntimeFixtureProfile(false, false, false, true, false)
	if !ok || !restart.device || restart.passkey || restart.expiry || !restart.restart || restart.grant || restart.limits.Concurrent != runtimeFixtureRestartConcurrent || restart.apiLimits.Concurrent != 2 || restart.limits.IdleSeconds != runtimeFixtureExpiryIdleSeconds || restart.limits.DurationSeconds != runtimeFixtureExpiryDurationSeconds || restart.apiLimits.IdleSeconds != runtimeFixtureExpiryIdleSeconds || restart.apiLimits.DurationSeconds != runtimeFixtureExpiryDurationSeconds || restart.gatewayMaxConcurrent != runtimeFixtureRestartMaxConcurrent || restart.gatewayMaxConcurrent != restart.limits.Concurrent+restart.apiLimits.Concurrent {
		t.Fatal("restart fixture did not retain its isolated stream capacity")
	}
	grant, ok := newRuntimeFixtureProfile(false, false, false, false, true)
	if !ok || !grant.device || grant.passkey || grant.expiry || grant.restart || !grant.grant || grant.limits != baseline.limits || grant.apiLimits != baseline.apiLimits || grant.gatewayMaxConcurrent != baseline.gatewayMaxConcurrent {
		t.Fatal("grant fixture did not retain its isolated lifecycle profile")
	}
	for _, incompatible := range [][5]bool{{false, true, false, true, false}, {false, false, true, true, false}, {false, true, false, false, true}, {false, false, true, false, true}, {false, false, false, true, true}} {
		if _, ok := newRuntimeFixtureProfile(incompatible[0], incompatible[1], incompatible[2], incompatible[3], incompatible[4]); ok {
			t.Fatal("restart fixture accepted an incompatible profile")
		}
	}
	client := runtimeFixtureClientDeclaration(expiry.apiLimits)
	if client.Rule.Limits != expiry.apiLimits || client.Validate() != nil {
		t.Fatal("expiry local client did not retain the API stream limits")
	}
	if administration, valid := runtimeFixtureBrowserAdministration(baseline, "human:device", "human:operator"); !valid || administration != nil {
		t.Fatal("baseline fixture unexpectedly configured browser administration")
	}
	if administration, valid := runtimeFixtureBrowserAdministration(expiry, "human:device", ""); !valid || administration == nil || administration.BrowserResource != "dash" || len(administration.Operators) != 1 || administration.Operators[0] != "human:device" {
		t.Fatal("expiry fixture did not bind browser administration to its device human")
	}
	if administration, valid := runtimeFixtureBrowserAdministration(passkey, "human:device", "human:operator"); !valid || administration == nil || administration.Operators[0] != "human:operator" {
		t.Fatal("passkey fixture did not retain its separate browser administrator")
	}
	if _, valid := runtimeFixtureBrowserAdministration(expiry, "", ""); valid {
		t.Fatal("expiry fixture accepted a missing device human")
	}
}

func TestRuntimeAutheliaPasskeyProfile(t *testing.T) {
	base := runtimeAutheliaConfig("127.0.0.1:1234", t.TempDir(), "users.yml", "client", "session", "storage", "validation", "hmac", "rsa", false, false)
	if strings.Contains(base, "webauthn:") || strings.Contains(base, "default_2fa_method") {
		t.Fatal("baseline fixture changed its TOTP-first selection")
	}
	passkey := runtimeAutheliaConfig("127.0.0.1:1234", t.TempDir(), "users.yml", "client", "session", "storage", "validation", "hmac", "rsa", true, false)
	for _, field := range []string{"enable_passkey_login: true", "experimental_enable_passkey_uv_two_factors: true", "discoverability: required", "user_verification: required", "timeout: '5 seconds'"} {
		if !strings.Contains(passkey, field) {
			t.Fatalf("passkey fixture missing %q", field)
		}
	}
	if strings.Contains(passkey, "default_2fa_method") {
		t.Fatal("passkey fixture must not force a WebAuthn bootstrap login")
	}
	grant := runtimeAutheliaConfig("127.0.0.1:1234", t.TempDir(), "users.yml", "client", "session", "storage", "validation", "hmac", "rsa", false, true)
	if !strings.Contains(grant, "https://"+edgeRetainedHost+"/_anvil-connect/callback") {
		t.Fatal("grant fixture missing retained-resource redirect")
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
	declaration := runtimeCaddyConfig(":443", "127.0.0.1:9091", "/tmp/ingress.sock", "certificate", "key", []string{dashHost, edgeRetainedHost})
	apps := declaration["apps"].(map[string]any)
	httpApp := apps["http"].(map[string]any)
	servers := httpApp["servers"].(map[string]any)
	server := servers["anvil_connect"].(map[string]any)
	routes := server["routes"].([]any)

	upgradeIndex, h2cIndex := -1, -1
	browserRoute := false
	for index, entry := range routes {
		route := entry.(map[string]any)
		matchers, ok := route["match"].([]any)
		if !ok || len(matchers) != 1 {
			continue
		}
		match := matchers[0].(map[string]any)
		hosts, ok := match["host"].([]string)
		if !ok {
			continue
		}
		versions := route["handle"].([]any)[1].(map[string]any)["transport"].(map[string]any)["versions"].([]string)
		if len(hosts) == 2 && hosts[0] == dashHost && hosts[1] == edgeRetainedHost && match["header_regexp"] != nil && len(versions) == 1 && versions[0] == "1.1" {
			browserRoute = true
		}
		if len(hosts) != 1 || hosts[0] != edgeAPIHost {
			continue
		}
		switch {
		case match["header_regexp"] != nil && len(versions) == 1 && versions[0] == "1.1":
			upgradeIndex = index
		case match["method"] != nil && len(versions) == 1 && versions[0] == "h2c":
			h2cIndex = index
		}
	}
	if !browserRoute || upgradeIndex < 0 || h2cIndex < 0 || upgradeIndex >= h2cIndex {
		t.Fatal("API upgrade route does not precede the API h2c route")
	}
}

func TestRuntimeCanonicalKeyID(t *testing.T) {
	for value, want := range map[string]bool{
		strings.Repeat("a", 32): true,
		strings.Repeat("A", 32): false,
		strings.Repeat("a", 31): false,
		strings.Repeat("g", 32): false,
	} {
		if got := runtimeCanonicalKeyID(value); got != want {
			t.Fatalf("canonical key id %q = %t, want %t", value, got, want)
		}
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
