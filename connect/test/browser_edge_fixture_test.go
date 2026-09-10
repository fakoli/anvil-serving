// Package browserfixture hosts the pinned Caddy and Authelia browser gate.
// It is a disposable fixture: its DNS and trust changes are confined to its
// own children and temporary directory.
package browserfixture

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha1" // #nosec G505 -- RFC6238 SHA-1 is Authelia's configured TOTP mode.
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base32"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testidentity"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
)

const edgeAuthHost = "auth.example.test"

type edgeFixture struct {
	edge          atomic.Pointer[httpedge.Browser]
	sessionBypass atomic.Bool
	nativeBypass  atomic.Bool
	dispatcher    *transport.Dispatcher
	resource      config.Resource
}

// edgeSocketPath reserves enough space for Linux sockaddr_un and turns an
// otherwise opaque net.Listen error into a fixed fixture-stage diagnostic.
func edgeSocketPath(directory string) (string, error) {
	path := filepath.Join(directory, "ingress.sock")
	if len(path) >= 104 {
		return "", errors.New("fixture socket path exceeds unix limit")
	}
	return path, nil
}

func TestEdgeSocketPathBounded(t *testing.T) {
	if _, err := edgeSocketPath(filepath.Join("/tmp", strings.Repeat("x", 110))); err == nil {
		t.Fatal("overlong fixture socket path accepted")
	}
	if path, err := edgeSocketPath("/tmp/ace-123"); err != nil || path != "/tmp/ace-123/ingress.sock" {
		t.Fatal("safe fixture socket path rejected")
	}
}

func (f *edgeFixture) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Host != dashHost {
		http.NotFound(w, r)
		return
	}
	if f.sessionBypass.Load() {
		// This is fixture-only and deliberately leaves transport/origin checks in
		// place. It proves the browser test detects a removed Connect session gate.
		f.dispatcher.BrowserDispatch(w, r, f.resource, session.Admission{SessionID: "fixture-bypass", SessionGeneration: 1, Principal: "fixture", PrincipalGeneration: 1, Resource: f.resource.Rule.ID, Host: dashHost, Epoch: "fixture", ExpiresAt: time.Now().Add(time.Minute)})
		return
	}
	if edge := f.edge.Load(); edge != nil {
		edge.ServeHTTP(w, r)
		return
	}
	http.Error(w, "fixture starting", http.StatusServiceUnavailable)
}

func (f *edgeFixture) nativeDashboard(w http.ResponseWriter, r *http.Request) {
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
			w.WriteHeader(http.StatusNoContent)
			return
		}
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

type edgeChild struct {
	cmd  *exec.Cmd
	done chan error
}

func edgeEnvironment(home string) []string {
	return append(os.Environ(), "HOME="+home, "XDG_CONFIG_HOME="+filepath.Join(home, "config"), "XDG_CACHE_HOME="+filepath.Join(home, "cache"), "XDG_DATA_HOME="+filepath.Join(home, "data"))
}

func edgePrepareHome(t *testing.T, home string) {
	t.Helper()
	for _, path := range []string{home, filepath.Join(home, "config"), filepath.Join(home, "cache"), filepath.Join(home, "data")} {
		if err := os.MkdirAll(path, 0700); err != nil {
			t.Fatal(err)
		}
	}
}

func startEdgeChild(t *testing.T, home, binary string, args ...string) *edgeChild {
	t.Helper()
	edgePrepareHome(t, home)
	cmd := exec.Command(binary, args...)
	cmd.Env = edgeEnvironment(home)
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	child := &edgeChild{cmd: cmd, done: make(chan error, 1)}
	go func() { child.done <- cmd.Wait() }()
	t.Cleanup(func() {
		if cmd.Process == nil {
			return
		}
		if err := cmd.Process.Signal(syscall.SIGTERM); err != nil && !errors.Is(err, os.ErrProcessDone) {
			t.Error("owned edge child stop failed")
			return
		}
		select {
		case err := <-child.done:
			if err != nil {
				t.Error("owned edge child exited unsuccessfully")
			}
		case <-time.After(5 * time.Second):
			_ = cmd.Process.Kill()
			select {
			case <-child.done:
				t.Error("owned edge child required forced termination")
			case <-time.After(2 * time.Second):
				t.Error("owned edge child did not exit")
			}
		}
	})
	return child
}

type edgeToolsLock struct {
	Schema     string `json:"schema"`
	Platform   string `json:"platform"`
	Components []struct {
		Name         string `json:"name"`
		Binary       string `json:"binary"`
		BinarySHA256 string `json:"binary_sha256"`
	} `json:"components"`
}

func edgeToolsDigest(name string) (string, error) {
	_, source, _, ok := runtime.Caller(0)
	if !ok {
		return "", errors.New("edge tool source path unavailable")
	}
	file, err := os.Open(filepath.Join(filepath.Dir(source), "..", "lab", "edge-tools.json"))
	if err != nil {
		return "", err
	}
	defer file.Close()
	var lock edgeToolsLock
	decoder := json.NewDecoder(io.LimitReader(file, 64*1024))
	if err := decoder.Decode(&lock); err != nil || lock.Schema != "anvil-connect.edge-tools/v1" || lock.Platform != "linux-amd64" {
		return "", errors.New("invalid edge tools lock")
	}
	var digest string
	for _, component := range lock.Components {
		if component.Name == name && component.Binary == name && regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(component.BinarySHA256) && digest == "" {
			digest = component.BinarySHA256
		} else if component.Name == name {
			return "", errors.New("ambiguous edge tool lock")
		}
	}
	if digest == "" {
		return "", errors.New("edge tool absent from lock")
	}
	return digest, nil
}

func verifiedEdgeTool(name, path string) (string, error) {
	expected, err := edgeToolsDigest(name)
	if err != nil {
		return "", err
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&0111 == 0 || info.Size() < 1 || info.Size() > 128*1024*1024 {
		return "", errors.New("edge tool is not a bounded executable file")
	}
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, io.LimitReader(file, 128*1024*1024+1)); err != nil || hex.EncodeToString(hash.Sum(nil)) != expected {
		return "", errors.New("edge tool digest mismatch")
	}
	return path, nil
}

func edgeTool(t *testing.T, name string) string {
	t.Helper()
	key := "ANVIL_CONNECT_EDGE_" + strings.ToUpper(name)
	path := os.Getenv(key)
	if path == "" {
		path = filepath.Join("/data/cache/anvil-connect/edge-tools/extract", name, name)
	}
	verified, err := verifiedEdgeTool(name, path)
	if err != nil {
		t.Fatalf("verified pinned %s binary is unavailable: %v", name, err)
	}
	return verified
}

func TestEdgeToolRejectsWrongDigest(t *testing.T) {
	path := filepath.Join(t.TempDir(), "caddy")
	if err := os.WriteFile(path, []byte("not-caddy"), 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := verifiedEdgeTool("caddy", path); err == nil {
		t.Fatal("wrong edge binary was accepted")
	}
}

func edgeReserve(t *testing.T) string {
	t.Helper()
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	return address
}

func edgeWrite(t *testing.T, path, value string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(value), 0600); err != nil {
		t.Fatal(err)
	}
}

func edgeRandom(t *testing.T, count int) string {
	t.Helper()
	data := make([]byte, count)
	if _, err := rand.Read(data); err != nil {
		t.Fatal(err)
	}
	return fmt.Sprintf("%x", data)
}

func edgeHash(t *testing.T, home, authelia string) (password, digest string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, authelia, "crypto", "hash", "generate", "argon2", "--profile", "low-memory", "--random", "--no-confirm")
	command.Env = edgeEnvironment(home)
	output, err := command.Output()
	if err != nil {
		t.Fatal("Authelia could not create a fixture password digest")
	}
	for _, line := range strings.Split(string(output), "\n") {
		if value, ok := strings.CutPrefix(line, "Random Password: "); ok {
			password = value
		}
		if value, ok := strings.CutPrefix(line, "Digest: "); ok {
			digest = value
		}
	}
	if password == "" || digest == "" {
		t.Fatal("Authelia did not return a fixture password digest")
	}
	return password, digest
}

func edgeRun(t *testing.T, home, binary string, args ...string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, binary, args...)
	command.Env = edgeEnvironment(home)
	if err := command.Run(); err != nil {
		t.Fatal("pinned Authelia fixture setup failed")
	}
}

func edgeCertificate(t *testing.T, names ...string) (certificate, key, root string, roots *x509.CertPool) {
	t.Helper()
	caKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	caTemplate := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: "Anvil Connect browser fixture CA"}, NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	leafKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	serial, err = rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		t.Fatal(err)
	}
	leaf := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: names[0]}, DNSNames: names, NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	leafDER, err := x509.CreateCertificate(rand.Reader, leaf, caTemplate, &leafKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := x509.ParseCertificate(caDER)
	if err != nil {
		t.Fatal(err)
	}
	roots = x509.NewCertPool()
	roots.AddCert(parsed)
	leafKeyDER, err := x509.MarshalPKCS8PrivateKey(leafKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: leafDER})), string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: leafKeyDER})), string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER})), roots
}

func edgeTOTP(secret string, now time.Time) string {
	key, err := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(secret)
	if err != nil {
		return ""
	}
	counter := uint64(now.Unix() / 30)
	var data [8]byte
	for i := range data {
		data[7-i] = byte(counter >> (8 * i))
	}
	mac := hmac.New(sha1.New, key) // #nosec G401 -- configured RFC6238 mode.
	_, _ = mac.Write(data[:])
	digest := mac.Sum(nil)
	offset := digest[len(digest)-1] & 0x0f
	value := (uint32(digest[offset])&0x7f)<<24 | uint32(digest[offset+1])<<16 | uint32(digest[offset+2])<<8 | uint32(digest[offset+3])
	return fmt.Sprintf("%06d", value%1000000)
}

// edgeStableTOTP avoids consuming a code during its final five seconds. The
// bounded alignment is part of the authentication exchange, not a blind wait.
func edgeStableTOTP(secret string) string {
	now := time.Now()
	remaining := 30 - int(now.Unix()%30)
	if remaining <= 5 {
		time.Sleep(time.Duration(remaining+1) * time.Second)
		now = time.Now()
	}
	return edgeTOTP(secret, now)
}

func edgeConfig(authListen, state, users, clientSecret, sessionSecret, storageKey, validationSecret, hmacSecret, rsaKey string) string {
	q := func(value string) string { encoded, _ := json.Marshal(value); return string(encoded) }
	template := func(path string, indent int) string {
		return fmt.Sprintf("{{- fileContent %s | nindent %d }}", q(path), indent)
	}
	return strings.Join([]string{
		"server:", "  address: " + q("tcp://"+authListen),
		"authentication_backend:", "  file:", "    path: " + q(users),
		"access_control:", "  default_policy: two_factor",
		"identity_validation:", "  reset_password:", "    jwt_secret: |-", "      " + template(validationSecret, 6),
		"notifier:", "  filesystem:", "    filename: " + q(filepath.Join(state, "notifications.txt")),
		"session:", "  secret: |-", "    " + template(sessionSecret, 4), "  cookies:", "    - domain: " + q(edgeAuthHost), "      authelia_url: " + q("https://"+edgeAuthHost),
		"storage:", "  encryption_key: |-", "    " + template(storageKey, 4), "  local:", "    path: " + q(filepath.Join(state, "authelia.sqlite3")),
		"identity_providers:", "  oidc:", "    hmac_secret: |-", "      " + template(hmacSecret, 6), "    jwks:", "      - key_id: 'anvil-connect-rs256'", "        algorithm: RS256", "        use: sig", "        key: |-", "          " + template(rsaKey, 10),
		"    clients:", "      - client_id: 'connect-browser'", "        client_secret: |-", "          " + template(clientSecret, 10), "        public: false", "        require_pkce: true", "        pkce_challenge_method: S256", "        response_types:", "          - code", "        grant_types:", "          - authorization_code", "        scopes:", "          - openid", "        id_token_signed_response_alg: RS256", "        token_endpoint_auth_method: client_secret_basic", "        redirect_uris:", "          - 'https://dash.example.test/_anvil-connect/callback'",
	}, "\n") + "\n"
}

func edgeCaddyConfig(listen, authListen, socket, certificate, key string) map[string]any {
	headers := []string{"Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP", "X-Anvil-Connect-User", "X-Anvil-Connect-Groups", "X-Auth-Request-User", "X-Auth-Request-Email", "X-Authenticated-User", "X-Authenticated-Groups", "Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name", "Cf-Access-Jwt-Assertion", "X-Goog-Authenticated-User", "X-Goog-Authenticated-User-Email", "X-Amzn-Oidc-Data", "X-Amzn-Oidc-Identity", "X-Amzn-Oidc-Accesstoken", "Tailscale-User-Login"}
	proxy := func(destination string, versions []string) map[string]any {
		return map[string]any{"handler": "reverse_proxy", "upstreams": []any{map[string]any{"dial": destination}}, "transport": map[string]any{"protocol": "http", "versions": versions}}
	}
	clean := map[string]any{"handler": "headers", "request": map[string]any{"delete": headers}}
	authRoute := map[string]any{"match": []any{map[string]any{"host": []string{edgeAuthHost}}}, "handle": []any{clean, proxy(authListen, []string{"1.1"})}}
	upgradeMatch := map[string]any{"header_regexp": map[string]any{"Connection": map[string]any{"pattern": `(?i)(^|,)[\t ]*upgrade[\t ]*(,|$)`}, "Upgrade": map[string]any{"pattern": `(?i)^websocket$`}}}
	websocketMatch := map[string]any{"host": []string{dashHost}, "header_regexp": upgradeMatch["header_regexp"]}
	websocketRoute := map[string]any{"match": []any{websocketMatch}, "handle": []any{clean, proxy("unix/"+socket, []string{"1.1"})}}
	ordinaryMatch := map[string]any{"host": []string{dashHost}, "method": []string{"GET", "POST"}}
	ordinaryRoute := map[string]any{"match": []any{ordinaryMatch}, "handle": []any{clean, proxy("unix/"+socket, []string{"h2c"})}}
	server := map[string]any{
		"listen":                  []string{listen},
		"tls_connection_policies": []any{map[string]any{}},
		"automatic_https":         map[string]any{"disable_redirects": true, "disable_certificates": true},
		"routes": []any{
			authRoute,
			websocketRoute,
			ordinaryRoute,
			map[string]any{"handle": []any{map[string]any{"handler": "static_response", "status_code": 404}}},
		},
	}
	return map[string]any{
		"admin": map[string]any{"disabled": true},
		"apps": map[string]any{
			"http": map[string]any{"servers": map[string]any{"anvil_connect": server}},
			"tls":  map[string]any{"certificates": map[string]any{"load_files": []any{map[string]any{"certificate": certificate, "key": key}}}, "disable_storage_clean": true},
		},
	}
}

func edgeGrantedSubject(home, binary, configuration, destination string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, binary, "storage", "user", "identifiers", "export", "--file", destination, "--config", configuration, "--config.experimental.filters", "template")
	command.Env = edgeEnvironment(home)
	if err := command.Run(); err != nil {
		return "", err
	}
	contents, err := os.ReadFile(destination)
	if err != nil || len(contents) > 8192 {
		return "", errors.New("identifier export unavailable")
	}
	identifiers := regexp.MustCompile(`(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b`).FindAllString(string(contents), -1)
	if len(identifiers) != 1 {
		return "", errors.New("identifier export is not a single public subject")
	}
	return strings.ToLower(identifiers[0]), nil
}

func TestBrowserEdgeFixture(t *testing.T) {
	if os.Getenv("ANVIL_CONNECT_BROWSER_EDGE_FIXTURE") != "1" {
		t.Skip("launched only by the actual-edge Playwright test")
	}
	caddy, authelia := edgeTool(t, "caddy"), edgeTool(t, "authelia")
	directory := t.TempDir()
	secrets, state, childHome := filepath.Join(directory, "secrets"), filepath.Join(directory, "state"), filepath.Join(directory, "child-home")
	for _, path := range []string{secrets, state, childHome} {
		if err := os.Mkdir(path, 0700); err != nil {
			t.Fatal(err)
		}
	}
	certificate, key, root, roots := edgeCertificate(t, edgeAuthHost, dashHost)
	certificatePath, keyPath, rootPath := filepath.Join(secrets, "edge.pem"), filepath.Join(secrets, "edge.key"), filepath.Join(secrets, "edge-root.pem")
	edgeWrite(t, certificatePath, certificate)
	edgeWrite(t, keyPath, key)
	edgeWrite(t, rootPath, root)
	edgePrepareHome(t, childHome)
	allowedPassword, allowedDigest := edgeHash(t, childHome, authelia)
	deniedPassword, deniedDigest := edgeHash(t, childHome, authelia)
	clientSecret, clientDigest := edgeHash(t, childHome, authelia)
	users := filepath.Join(secrets, "users.yml")
	edgeWrite(t, users, "users:\n  fixture-allowed:\n    displayname: Fixture Allowed\n    password: "+fmt.Sprintf("%q", allowedDigest)+"\n    email: fixture-allowed@example.test\n    groups: []\n  fixture-denied:\n    displayname: Fixture Denied\n    password: "+fmt.Sprintf("%q", deniedDigest)+"\n    email: fixture-denied@example.test\n    groups: []\n")
	clientSecretPath := filepath.Join(secrets, "client-secret")
	edgeWrite(t, clientSecretPath, clientDigest+"\n")
	secretPaths := map[string]string{}
	for _, name := range []string{"session", "storage", "validation", "hmac"} {
		path := filepath.Join(secrets, name)
		edgeWrite(t, path, edgeRandom(t, 48)+"\n")
		secretPaths[name] = path
	}
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
	authConfig := filepath.Join(directory, "authelia.yml")
	edgeWrite(t, authConfig, edgeConfig(authListen, state, users, clientSecretPath, secretPaths["session"], secretPaths["storage"], secretPaths["validation"], secretPaths["hmac"], oidcPath))
	edgeRun(t, childHome, authelia, "storage", "migrate", "up", "--config", authConfig, "--config.experimental.filters", "template")
	allowedTOTP := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString([]byte(edgeRandom(t, 20)))
	deniedTOTP := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString([]byte(edgeRandom(t, 20)))
	for _, item := range []struct{ user, secret string }{{"fixture-allowed", allowedTOTP}, {"fixture-denied", deniedTOTP}} {
		edgeRun(t, childHome, authelia, "storage", "user", "totp", "generate", item.user, "--secret", item.secret, "--issuer", "Anvil Connect Fixture", "--algorithm", "SHA1", "--digits", "6", "--period", "30", "--config", authConfig, "--config.experimental.filters", "template")
	}
	startEdgeChild(t, childHome, authelia, "--config", authConfig, "--config.experimental.filters", "template")

	fixture := &edgeFixture{}
	native := httptest.NewServer(http.HandlerFunc(fixture.nativeDashboard))
	defer native.Close()
	ca := testpki.New(t)
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
	socket, err := edgeSocketPath(directory)
	if err != nil {
		t.Fatal("fixture socket path is unsafe")
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	protocols := new(http.Protocols)
	protocols.SetHTTP1(true)
	protocols.SetUnencryptedHTTP2(true)
	ingress := &http.Server{Handler: fixture, Protocols: protocols, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	go func() { _ = ingress.Serve(listener) }()
	defer ingress.Close()
	caddyConfig, err := json.Marshal(edgeCaddyConfig(caddyListen, authListen, socket, certificatePath, keyPath))
	if err != nil {
		t.Fatal(err)
	}
	caddyConfigPath := filepath.Join(directory, "caddy.json")
	edgeWrite(t, caddyConfigPath, string(caddyConfig))
	startEdgeChild(t, childHome, caddy, "run", "--config", caddyConfigPath)

	dialAddress := caddyListen
	oidcTransport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots, ServerName: edgeAuthHost}, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != edgeAuthHost+":443" {
			return nil, &net.AddrError{Err: "fixture issuer destination denied", Addr: address}
		}
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", dialAddress)
	}}
	defer oidcTransport.CloseIdleConnections()
	deadline := time.Now().Add(15 * time.Second)
	for {
		response, probeErr := (&http.Client{Transport: oidcTransport, Timeout: 2 * time.Second}).Get("https://" + edgeAuthHost + "/api/health")
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
	stateStore, err := store.Open(filepath.Join(directory, "session"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer stateStore.Close()
	issuerURL := "https://" + edgeAuthHost
	manager, err := session.New(context.Background(), stateStore, []config.Rule{gateway.Resources[0].Rule}, session.Config{Issuer: issuerURL, ClientID: "connect-browser", ClientSecret: clientSecret, CallbackPath: httpedge.BrowserCallbackPath, TransactionLifetime: session.DefaultTransactionLifetime, SessionLifetime: time.Hour, MaxTransactions: 8, MaxPerBrowser: 2, HTTPClient: &http.Client{Transport: oidcTransport}})
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close()
	edge, err := httpedge.NewBrowser(gateway, manager, dispatcher.BrowserDispatch)
	if err != nil {
		t.Fatal(err)
	}
	defer edge.Close()
	fixture.edge.Store(edge)
	ready := map[string]string{"url": "https://" + dashHost, "resolver": caddyListen, "ca": rootPath, "allowed_user": "fixture-allowed", "allowed_password": allowedPassword, "denied_user": "fixture-denied", "denied_password": deniedPassword}
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
		case "grant allowed":
			subject, grantErr := edgeGrantedSubject(childHome, authelia, authConfig, filepath.Join(directory, "identifiers.yml"))
			if grantErr != nil {
				response["error"] = "fixture subject export failed"
			} else if _, grantErr = manager.SetHuman(issuerURL, subject, []string{"dash"}, false); grantErr != nil {
				response["error"] = "fixture Connect grant failed"
			}
		case "totp allowed":
			response["code"] = edgeStableTOTP(allowedTOTP)
		case "totp denied":
			response["code"] = edgeStableTOTP(deniedTOTP)
		case "mode session-bypass":
			fixture.sessionBypass.Store(true)
		case "mode session-enforce":
			fixture.sessionBypass.Store(false)
		case "mode native-bypass":
			fixture.nativeBypass.Store(true)
		default:
			t.Errorf("unknown browser edge command")
		}
		_ = json.NewEncoder(os.Stdout).Encode(response)
	}
}
