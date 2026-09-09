package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/localhttp"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	connectruntime "github.com/fakoli/anvil-serving/connect/internal/runtime"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"golang.org/x/sys/unix"
)

func cliRule() config.Rule {
	return config.Rule{ID: "router", Host: "router.example.test", PathPrefix: "/v1", Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer", Limits: config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 3}}
}

func jsonFile(t *testing.T, name string, value any) string {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func noSecrets(t *testing.T) func(string) (string, bool) {
	return func(string) (string, bool) { t.Error("unexpected secret lookup"); return "", false }
}

func TestValidateHasNoPrivateStateOrSecretEffects(t *testing.T) {
	root := filepath.Join(t.TempDir(), "absent")
	c := connectruntime.GatewayConfig{Schema: "anvil-connect.gateway-runtime/v1", Gateway: config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:18100", MaxConcurrent: 2, Resources: []config.Resource{{Rule: cliRule(), Connector: "origin", TunnelAddress: "127.0.0.1:18101"}}}, ControlHost: "control.example.test", TunnelHost: "tunnel.example.test", StateDirectory: root, TunnelBinary: "/missing/wstunnel", TunnelListen: "127.0.0.1:18102"}
	path := jsonFile(t, "gateway.json", c)
	var stdout, stderr bytes.Buffer
	if code := run(context.Background(), []string{"validate", "--mode", "gateway", "--config", path}, &stdout, &stderr, noSecrets(t)); code != 0 {
		t.Fatalf("validate code %d", code)
	}
	if !strings.Contains(stdout.String(), `"status":"valid"`) || stderr.Len() != 0 {
		t.Fatal("missing validation status")
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatal("validate created runtime state")
	}
	for _, args := range [][]string{
		{"validate", "--mode", "unknown", "--config", path},
		{"validate", "--mode", "gateway", "--config", path, "--unknown-secret-value"},
		{"validate", "--mode", "gateway", "--config", path, "extra"},
		{"init", "--mode", "gateway", "--config", path, "--bundle", path},
		{"client", "--mode", "client", "--config", path},
	} {
		stdout.Reset()
		stderr.Reset()
		if run(context.Background(), args, &stdout, &stderr, noSecrets(t)) != 2 {
			t.Fatal("invalid command accepted")
		}
		if stdout.Len() != 0 || strings.Contains(stderr.String(), "unknown-secret-value") {
			t.Fatal("invalid command echoed input")
		}
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatal("invalid command created runtime state")
	}
}

func TestDeclarationRejectsFIFOUnsafeModeSymlinkAndOversize(t *testing.T) {
	root := t.TempDir()
	safe := filepath.Join(root, "safe")
	if err := os.WriteFile(safe, []byte("{}"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(safe, filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}
	if err := unix.Mkfifo(filepath.Join(root, "fifo"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "unsafe"), []byte("{}"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Join(root, "unsafe"), 0666); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "large"), make([]byte, 1024*1024+1), 0600); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"link", "fifo", "unsafe", "large"} {
		if _, err := readDeclaration(filepath.Join(root, name)); err == nil {
			t.Fatalf("accepted %s", name)
		}
	}
	if _, err := readDeclaration(safe); err != nil {
		t.Fatal("valid config rejected")
	}
}

func TestLocalKeyOutputExclusiveAndPrivate(t *testing.T) {
	path := filepath.Join(t.TempDir(), "private", "local-key")
	var stdout, stderr bytes.Buffer
	args := []string{"keygen", "--output", path}
	if run(context.Background(), args, &stdout, &stderr, noSecrets(t)) != 0 {
		t.Fatal("key generation failed")
	}
	key, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(stdout.String(), strings.TrimSpace(string(key))) || stderr.Len() != 0 {
		t.Fatal("secret exposed in output")
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("unsafe output mode")
	}
	forwarder, err := client.New(cliRule(), "127.0.0.1:18200", strings.TrimSpace(string(key)), "TEST_REMOTE_KEY", noSecrets(t), client.Options{})
	if err != nil {
		t.Fatal("generated key not usable by forwarder")
	}
	forwarder.Close()
	if run(context.Background(), args, &stdout, &stderr, noSecrets(t)) == 0 {
		t.Fatal("existing output replaced")
	}
	after, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(after, key) {
		t.Fatal("existing key changed")
	}
}

func TestAdminReservesOutputBeforeMutationAndNeverPrintsKey(t *testing.T) {
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer state.Close()
	keys, err := access.NewKeys(state, []config.Rule{cliRule()})
	if err != nil {
		t.Fatal(err)
	}
	manager, err := identity.NewManager(state, "https://control.example.test", []string{"router"})
	if err != nil {
		t.Fatal(err)
	}
	handler, err := admin.New(state, keys, manager, nil)
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(t.TempDir(), "runtime")
	directory, err := privatefiles.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	listener, err := localhttp.Listen(directory, "admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	var calls atomic.Int64
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); handler.ServeHTTP(w, r) }), ReadHeaderTimeout: time.Second}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	defer func() { server.Close(); <-done }()
	socket := filepath.Join(root, "admin.sock")
	invoke := func(input admin.Request, output string) (int, string, string) {
		input.Grants = append([]access.Grant{}, input.Grants...)
		input.Resources = append([]string{}, input.Resources...)
		request := jsonFile(t, "request.json", input)
		args := []string{"admin", "--socket", socket, "--request", request}
		if output != "" {
			args = append(args, "--output", output)
		}
		var stdout, stderr bytes.Buffer
		code := run(context.Background(), args, &stdout, &stderr, noSecrets(t))
		return code, stdout.String(), stderr.String()
	}
	grants := []access.Grant{{Resource: "router", Methods: []string{"GET"}}}
	if code, _, _ := invoke(admin.Request{Operation: "principal-set", Principal: "owner", Grants: grants}, ""); code != 0 {
		t.Fatal("principal setup failed")
	}
	issue := admin.Request{Operation: "api-key-issue", Principal: "owner", Grants: grants, LifetimeSeconds: 60}
	before := calls.Load()
	if code, _, _ := invoke(issue, ""); code != 2 || calls.Load() != before {
		t.Fatal("credential RPC sent without output reservation")
	}
	output := filepath.Join(root, "issued.json")
	if err := os.WriteFile(output, []byte("preserve"), 0600); err != nil {
		t.Fatal(err)
	}
	if code, _, _ := invoke(issue, output); code == 0 || calls.Load() != before {
		t.Fatal("credential RPC sent with occupied output")
	}
	if data, _ := os.ReadFile(output); string(data) != "preserve" {
		t.Fatal("occupied output changed")
	}
	output = filepath.Join(root, "new.json")
	code, stdout, stderr := invoke(issue, output)
	if code != 0 || calls.Load() != before+1 {
		t.Fatal("key issue failed")
	}
	data, err := os.ReadFile(output)
	var response admin.Response
	if err != nil || json.Unmarshal(data, &response) != nil || response.Secret == "" {
		t.Fatal("no secret in private output")
	}
	if strings.Contains(stdout, response.Secret) || strings.Contains(stderr, response.Secret) {
		t.Fatal("credential leaked to process output")
	}
	if _, err := keys.Authenticate(response.Secret, "router", "GET"); err != nil {
		t.Fatal("issued key cannot authenticate")
	}
}

func TestClientCancellationClosesOwnedListener(t *testing.T) {
	probe, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := probe.Addr().String()
	probe.Close()
	key, err := client.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	c := connectruntime.ClientConfig{Schema: "anvil-connect.client-runtime/v1", Rule: cliRule(), Listen: address, LocalKeyEnv: "TEST_LOCAL_KEY", RemoteKeyEnv: "TEST_REMOTE_KEY"}
	path := jsonFile(t, "client.json", c)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	reader, writer := io.Pipe()
	done := make(chan int, 1)
	go func() {
		done <- run(ctx, []string{"client", "--config", path}, writer, io.Discard, func(name string) (string, bool) {
			if name == "TEST_LOCAL_KEY" {
				return key, true
			}
			return "", false
		})
		writer.Close()
	}()
	decoder := json.NewDecoder(reader)
	var state map[string]string
	if err := decoder.Decode(&state); err != nil || state["status"] != "running" {
		t.Fatal("client failed to start")
	}
	cancel()
	if err := decoder.Decode(&state); err != nil || state["status"] != "stopped" {
		t.Fatal("client failed to stop")
	}
	if code := <-done; code != 0 {
		t.Fatal("client cancellation failed")
	}
	reader.Close()
	rebound, err := net.Listen("tcp4", address)
	if err != nil {
		t.Fatal("client listener leaked")
	}
	rebound.Close()
}

func TestGatewayStoppedStatusFollowsOwnedCleanup(t *testing.T) {
	binary := os.Getenv("ANVIL_CONNECT_WSTUNNEL")
	if binary == "" {
		t.Skip("requires explicit pinned transport artifact")
	}
	address := func() string {
		listener, err := net.Listen("tcp4", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		result := listener.Addr().String()
		listener.Close()
		return result
	}
	root := filepath.Join(t.TempDir(), "gateway")
	c := connectruntime.GatewayConfig{Schema: "anvil-connect.gateway-runtime/v1", Gateway: config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: address(), MaxConcurrent: 2, Resources: []config.Resource{{Rule: cliRule(), Connector: "origin", TunnelAddress: address()}}}, ControlHost: "control.example.test", TunnelHost: "tunnel.example.test", StateDirectory: root, TunnelBinary: binary, TunnelListen: address()}
	path := jsonFile(t, "gateway.json", c)
	if code := run(context.Background(), []string{"init", "--mode", "gateway", "--config", path}, io.Discard, io.Discard, noSecrets(t)); code != 0 {
		t.Fatal("initialization failed")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	reader, writer := io.Pipe()
	// Keep run inside its final status write until the observer has checked
	// cleanup. This makes a deferred-only Close fail deterministically.
	finishStatus := make(chan struct{})
	var finishOnce sync.Once
	releaseStatus := func() { finishOnce.Do(func() { close(finishStatus) }) }
	defer releaseStatus()
	output := &gatedStoppedWriter{Writer: writer, finish: finishStatus}
	done := make(chan int, 1)
	go func() {
		done <- run(ctx, []string{"gateway", "--config", path}, output, io.Discard, func(string) (string, bool) { return "", false })
		writer.Close()
	}()
	decoder := json.NewDecoder(reader)
	var state map[string]string
	if err := decoder.Decode(&state); err != nil || state["status"] != "running" {
		t.Fatal("gateway failed to start")
	}
	if _, err := os.Lstat(filepath.Join(root, "ingress.sock")); err != nil {
		t.Fatal("gateway ingress did not open")
	}
	cancel()
	if err := decoder.Decode(&state); err != nil || state["status"] != "stopped" {
		t.Fatal("gateway failed to stop")
	}
	// Check immediately when the stopped event is consumed, before waiting for
	// run to return. A deferred-only Close incorrectly emits this event early.
	for _, name := range []string{"ingress.sock", "admin.sock"} {
		if _, err := os.Lstat(filepath.Join(root, name)); !os.IsNotExist(err) {
			t.Fatal("stopped status preceded owned socket cleanup")
		}
	}
	rebound, err := net.Listen("tcp4", c.TunnelListen)
	if err != nil {
		t.Fatal("stopped status preceded child tunnel cleanup")
	}
	rebound.Close()
	releaseStatus()
	if code := <-done; code != 0 {
		t.Fatal("gateway cancellation failed")
	}
	reader.Close()
}

type gatedStoppedWriter struct {
	io.Writer
	finish <-chan struct{}
}

func (w *gatedStoppedWriter) Write(data []byte) (int, error) {
	n, err := w.Writer.Write(data)
	if bytes.Contains(data, []byte(`"status":"stopped"`)) {
		<-w.finish
	}
	return n, err
}
