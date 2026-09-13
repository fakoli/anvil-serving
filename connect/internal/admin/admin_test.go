package admin

import (
	"bufio"
	"context"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/localhttp"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

func testAdmin(t *testing.T) (*store.Store, *access.Keys, *identity.Manager, string) {
	t.Helper()
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = state.Close() })
	rule := config.Rule{
		ID: "router", Host: "router.example.test", PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer",
		Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 1},
	}
	keys, err := access.NewKeys(state, []config.Rule{rule})
	if err != nil {
		t.Fatal(err)
	}
	identities, err := identity.NewManager(state, "https://connect.example.test", []string{"router"})
	if err != nil {
		t.Fatal(err)
	}
	handler, err := New(state, keys, identities, nil)
	if err != nil {
		t.Fatal(err)
	}
	directory, err := privatefiles.Open(filepath.Join(t.TempDir(), "runtime"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = directory.Close() })
	listener, err := localhttp.Listen(directory, "admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	pinned, err := directory.PinPath("admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: handler, ReadHeaderTimeout: time.Second}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	t.Cleanup(func() {
		_ = server.Close()
		_ = listener.Close()
		_ = pinned.Close()
		select {
		case <-done:
		case <-time.After(time.Second):
			t.Error("local admin server did not stop")
		}
	})
	return state, keys, identities, pinned.Path()
}

func TestCallIssuesAndRevokesKey(t *testing.T) {
	_, keys, _, socket := testAdmin(t)
	context := context.Background()
	if _, err := Call(context, socket, Request{Operation: "principal-set", Principal: "owner", Grants: []access.Grant{{Resource: "router", Methods: []string{"GET", "POST"}}}}); err != nil {
		t.Fatalf("principal-set: %v", err)
	}
	issued, err := Call(context, socket, Request{Operation: "api-key-issue", Principal: "owner", Grants: []access.Grant{{Resource: "router", Methods: []string{"POST"}}}, LifetimeSeconds: 60})
	if err != nil || issued.Secret == "" || issued.KeyID == "" {
		t.Fatalf("api-key-issue = %#v, %v", issued, err)
	}
	if _, err := keys.Authenticate(issued.Secret, "router", "POST"); err != nil {
		t.Fatalf("issued key did not authenticate: %v", err)
	}
	if _, err := Call(context, socket, Request{Operation: "api-key-revoke", KeyID: issued.KeyID}); err != nil {
		t.Fatalf("api-key-revoke: %v", err)
	}
	if _, err := keys.Authenticate(issued.Secret, "router", "POST"); err == nil {
		t.Fatal("revoked key authenticated")
	}
}

func TestAdministrativeOperationVocabularyIsClosed(t *testing.T) {
	if !ValidOperation("human-suspend") || ValidOperation("human-delete") {
		t.Fatal("administrative operation vocabulary changed")
	}
}

func TestHumanSuspendUsesOnlyIssuerAndSubject(t *testing.T) {
	state, keys, identities, _ := testAdmin(t)
	var issuer *httptest.Server
	issuer = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"issuer": issuer.URL, "authorization_endpoint": issuer.URL + "/authorize", "token_endpoint": issuer.URL + "/token", "jwks_uri": issuer.URL + "/keys"})
	}))
	t.Cleanup(issuer.Close)
	rule := config.Rule{ID: "dashboard", Host: "dash.example.test", PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none", Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 1}}
	sessions, err := session.New(context.Background(), state, []config.Rule{rule}, session.Config{Issuer: issuer.URL, ClientID: "test-client", ClientSecret: "test-secret", CallbackPath: "/_connect/callback", TransactionLifetime: time.Minute, SessionLifetime: time.Hour, MaxTransactions: 1, MaxPerBrowser: 1, HTTPClient: issuer.Client()})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(sessions.Close)
	human, err := sessions.SetHuman(issuer.URL, "target", []string{"dashboard"}, false)
	if err != nil {
		t.Fatal(err)
	}
	handler, err := New(state, keys, identities, sessions)
	if err != nil {
		t.Fatal(err)
	}
	directory, err := privatefiles.Open(filepath.Join(t.TempDir(), "runtime"))
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	listener, err := localhttp.Listen(directory, "admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	pinned, err := directory.PinPath("admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer pinned.Close()
	server := &http.Server{Handler: handler, ReadHeaderTimeout: time.Second}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	defer func() {
		_ = server.Close()
		<-done
	}()
	response, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-suspend", Issuer: issuer.URL, Subject: "target"})
	if err != nil || response.Principal != human.ID || response.Generation != human.Generation+1 || len(response.Resources) != 1 || response.Resources[0] != "dashboard" {
		t.Fatalf("human suspend response = %#v, %v", response, err)
	}
	if _, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-suspend", Issuer: issuer.URL, Subject: "unprovisioned", Resources: []string{"dashboard"}}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("human suspend accepted extra fields: %v", err)
	}
	created, err := sessions.SetHuman(issuer.URL, "unprovisioned", []string{"dashboard"}, false)
	if err != nil || created.Generation != 1 {
		t.Fatalf("rejected request provisioned a grant: %#v, %v", created, err)
	}
}

func TestAdminRejectsOtherHostAndMalformedBody(t *testing.T) {
	state, _, _, socket := testAdmin(t)
	before := epoch(t, state)
	for _, input := range []struct{ host, body string }{
		{"other.anvil-connect.internal", `{"operation":"status"}`},
		{Host, `{"operation":"status","operation":"authority-reset"}`},
	} {
		status := rawRequest(t, socket, input.host, input.body)
		if status != http.StatusBadRequest {
			t.Fatalf("other/malformed request status = %d", status)
		}
	}
	if after := epoch(t, state); after != before {
		t.Fatalf("malformed request changed epoch: %q -> %q", before, after)
	}
}

func rawRequest(t *testing.T, socket, host, body string) int {
	t.Helper()
	connection, err := net.Dial("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	if _, err := fmt.Fprintf(connection, "POST %s HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s", Path, host, len(body), body); err != nil {
		t.Fatal(err)
	}
	request, _ := http.NewRequest(http.MethodPost, "http://"+host+Path, nil)
	response, err := http.ReadResponse(bufio.NewReader(connection), request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	return response.StatusCode
}

func epoch(t *testing.T, state *store.Store) string {
	t.Helper()
	var result string
	if err := state.View(func(tx *store.Tx) error { result = tx.Epoch(); return nil }); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestCallRejectsNonSocketAndResponseMismatch(t *testing.T) {
	directory, err := privatefiles.Open(filepath.Join(t.TempDir(), "runtime"))
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	pinned, err := directory.PinPath("not-a-socket")
	if err != nil {
		t.Fatal(err)
	}
	defer pinned.Close()
	if err := os.WriteFile(pinned.Path(), []byte("not socket"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := Call(context.Background(), pinned.Path(), Request{Operation: "status"}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("Call non-socket error = %v, want ErrAdmin", err)
	}
}

func TestInviteReturnsOnlyValidatedPublicBootstrap(t *testing.T) {
	state, keys, identities, _ := testAdmin(t)
	ca := testpki.New(t)
	options := Options{ControlHost: "control.example.test", TunnelHost: "tunnel.example.test", InnerCAPEM: string(ca.PEM())}
	handler, err := New(state, keys, identities, nil, options)
	if err != nil {
		t.Fatal(err)
	}
	response, err := handler.apply(Request{Operation: "invite", Installation: "connector-a", Role: "connector", Resources: []string{"router"}, LifetimeSeconds: 60})
	if err != nil || response.Invitation == "" || response.ControlHost != options.ControlHost || response.TunnelHost != options.TunnelHost || response.InnerCAPEM != options.InnerCAPEM {
		t.Fatalf("invite bootstrap = %#v, %v", response, err)
	}
	if _, err := New(state, keys, identities, nil, Options{ControlHost: options.ControlHost, TunnelHost: options.TunnelHost, InnerCAPEM: "-----BEGIN PRIVATE KEY-----\ninvalid\n-----END PRIVATE KEY-----\n"}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("private bootstrap block accepted: %v", err)
	}
	block, _ := pem.Decode(ca.PEM())
	block.Headers = map[string]string{"Private-Material": "must-not-enter-invitation"}
	options.InnerCAPEM = string(pem.EncodeToMemory(block))
	if _, err := New(state, keys, identities, nil, options); !errors.Is(err, ErrAdmin) {
		t.Fatal("PEM headers crossed public bootstrap boundary")
	}
}
