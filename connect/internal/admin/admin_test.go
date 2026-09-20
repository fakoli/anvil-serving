package admin

import (
	"bufio"
	"bytes"
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
	if !ValidOperation("human-suspend") || !ValidOperation("human-revoke-sessions") || ValidOperation("human-delete") {
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
	revoked, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-revoke-sessions", Issuer: issuer.URL, Subject: "target"})
	if err != nil || revoked.Principal != human.ID || revoked.Generation != human.Generation+1 || len(revoked.Resources) != 1 || revoked.Resources[0] != "dashboard" {
		t.Fatalf("human revoke response = %#v, %v", revoked, err)
	}
	response, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-suspend", Issuer: issuer.URL, Subject: "target"})
	if err != nil || response.Principal != human.ID || response.Generation != human.Generation+2 || len(response.Resources) != 1 || response.Resources[0] != "dashboard" {
		t.Fatalf("human suspend response = %#v, %v", response, err)
	}
	missing, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-suspend", Issuer: issuer.URL, Subject: "missing"})
	if err != nil || !config.ValidHumanID(missing.Principal) || missing.Generation != 0 || len(missing.Resources) != 0 || missing.ApplicationRoles != nil {
		t.Fatalf("missing human suspend response = %#v, %v", missing, err)
	}
	missing, err = Call(context.Background(), pinned.Path(), Request{Operation: "human-revoke-sessions", Issuer: issuer.URL, Subject: "missing"})
	if err != nil || !config.ValidHumanID(missing.Principal) || missing.Generation != 0 || len(missing.Resources) != 0 || missing.ApplicationRoles != nil {
		t.Fatalf("missing human revoke response = %#v, %v", missing, err)
	}
	if _, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-suspend", Issuer: issuer.URL, Subject: "unprovisioned", Resources: []string{"dashboard"}}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("human suspend accepted extra fields: %v", err)
	}
	if _, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-revoke-sessions", Issuer: issuer.URL, Subject: "unprovisioned", Resources: []string{"dashboard"}}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("human revoke accepted extra fields: %v", err)
	}
	created, err := sessions.SetHuman(issuer.URL, "unprovisioned", []string{"dashboard"}, false)
	if err != nil || created.Generation != 1 {
		t.Fatalf("rejected request provisioned a grant: %#v, %v", created, err)
	}
	inspected, err := handler.apply(Request{Operation: "human-inspect", Principal: created.ID})
	if err != nil || inspected.Found == nil || !*inspected.Found || inspected.Human == nil || inspected.Human.ID != created.ID || inspected.Human.Generation != created.Generation {
		t.Fatalf("existing human inspect response = %#v, %v", inspected, err)
	}
	missingID := "human:0000000000000000000000000000000000000000000000000000000000000000"
	missingInspect, err := handler.apply(Request{Operation: "human-inspect", Principal: missingID})
	if err != nil || missingInspect.Found == nil || *missingInspect.Found || missingInspect.Human != nil {
		t.Fatalf("absent human inspect response = %#v, %v", missingInspect, err)
	}
	missingWire, err := json.Marshal(missingInspect)
	if err != nil || !bytes.Contains(missingWire, []byte(`"found":false`)) || bytes.Contains(missingWire, []byte(`"human"`)) {
		t.Fatalf("absent human inspect wire shape = %s, %v", missingWire, err)
	}
	expectedAbsentWire := `{"operation":"human-inspect","epoch":"","secret":"","key_id":"","principal":"","grants":[],"invitation":"","installation":"","role":"","resources":[],"generation":0,"fingerprint":"","found":false,"status":{"id":"","status":"","fingerprint":"","epoch":"","generation":0,"resources":[]}}`
	if string(missingWire) != expectedAbsentWire {
		t.Fatalf("absent human inspect envelope = %s", missingWire)
	}
	if _, err := handler.apply(Request{Operation: "human-inspect", Principal: "malformed"}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("malformed inspect principal accepted: %v", err)
	}
	if _, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-delete-prepare-absent", Principal: created.ID, Username: "unprovisioned", RequestID: "c2f1e253-cd05-4e26-9b27-284a20e4aa7f"}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("existing principal accepted as native-absent deletion: %v", err)
	}
	fenced, err := sessions.SetHuman(issuer.URL, "fence-subject", []string{"dashboard"}, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := state.Update(func(tx *store.Tx) error { return tx.Delete("principals", fenced.ID) }); err != nil {
		t.Fatal(err)
	}
	requestID := "d2f1e253-cd05-4e26-9b27-284a20e4aa7f"
	prepared, err := handler.apply(Request{Operation: "human-delete-prepare-absent", Principal: fenced.ID, Username: "idp-only", RequestID: requestID, usernamePresent: true})
	if err != nil || prepared.Deletion == nil || prepared.Deletion.Principal != fenced.ID || prepared.Deletion.Username != "idp-only" || prepared.Deletion.Generation != 2 {
		t.Fatalf("native-absent deletion fence = %#v, %v", prepared, err)
	}
	wirePrepared, err := json.Marshal(prepared)
	if err != nil || !bytes.Contains(wirePrepared, []byte(`"completed_at":"0001-01-01T00:00:00Z"`)) {
		t.Fatalf("pending native-absent deletion wire timestamp = %s, %v", wirePrepared, err)
	}
	retry, err := Call(context.Background(), pinned.Path(), Request{Operation: "human-delete-prepare-absent", Principal: fenced.ID, Username: "idp-only", RequestID: requestID})
	if err != nil || retry.Deletion == nil || retry.Deletion.RequestID != requestID || retry.Deletion.Generation != 2 {
		t.Fatalf("native-absent deletion retry = %#v, %v", retry, err)
	}
	if _, err := sessions.SetHuman(issuer.URL, "fence-subject", []string{"dashboard"}, false); !errors.Is(err, session.ErrConflict) {
		t.Fatalf("native-absent deletion did not fence concurrent provisioning: %v", err)
	}
	if err := sessions.FinalizeDeletion(requestID, fenced.ID, 2); err != nil {
		t.Fatal(err)
	}
	fresh, err := sessions.SetHuman(issuer.URL, "fence-subject", []string{"dashboard"}, false)
	if err != nil || fresh.ID != fenced.ID || fresh.Generation != 1 {
		t.Fatalf("finalized native-absent tombstone did not release identity: %#v, %v", fresh, err)
	}
	for _, request := range []Request{
		{Operation: "human-inspect", Principal: created.ID},
		{Operation: "human-deletions"},
		{Operation: "human-delete-prepare", Principal: created.ID, ExpectedGeneration: created.Generation, RequestID: "b2f1e253-cd05-4e26-9b27-284a20e4aa7f"},
		{Operation: "human-delete-finalize", Principal: created.ID, ExpectedGeneration: created.Generation + 1, RequestID: "b2f1e253-cd05-4e26-9b27-284a20e4aa7f"},
	} {
		request.Issuer = issuer.URL
		if _, err := handler.apply(request); !errors.Is(err, ErrAdmin) {
			t.Fatalf("%s accepted ignored issuer: %v", request.Operation, err)
		}
	}
	if unchanged, err := sessions.InspectHuman(created.ID); err != nil || unchanged.Disabled || unchanged.Generation != created.Generation {
		t.Fatalf("rejected deletion changed authority: %#v, %v", unchanged, err)
	}
	empty, err := handler.apply(Request{Operation: "human-deletions"})
	if err != nil {
		t.Fatal(err)
	}
	wire, err := json.Marshal(empty)
	if err != nil || !bytes.Contains(wire, []byte(`"deletions":[]`)) {
		t.Fatalf("empty deletion queue lost its array contract: %s, %v", wire, err)
	}
	name := "target.user"
	set, err := handler.apply(Request{Operation: "human-set", Issuer: issuer.URL, Subject: "target", Username: name, Resources: []string{"dashboard"}})
	if err != nil || set.Username != name || set.Principal != human.ID {
		t.Fatalf("human-set username response = %#v, %v", set, err)
	}
	retained, err := handler.apply(Request{Operation: "human-set", Issuer: issuer.URL, Subject: "target", Resources: []string{"dashboard"}})
	if err != nil || retained.Username != name {
		t.Fatalf("omitted username did not retain metadata: %#v, %v", retained, err)
	}
	if _, err := handler.apply(Request{Operation: "human-set", Issuer: issuer.URL, Subject: "target", Username: "Target", Resources: []string{"dashboard"}}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("invalid native username accepted: %v", err)
	}
	if _, err := handler.apply(Request{Operation: "human-suspend", Issuer: issuer.URL, Subject: "target", Username: name}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("username was accepted outside human-set: %v", err)
	}
	for _, raw := range [][]byte{
		[]byte(`{"operation":"human-set","username":""}`),
		[]byte(`{"operation":"human-set","username":null}`),
	} {
		var request Request
		if err := config.Decode(bytes.NewReader(raw), &request); err == nil {
			t.Fatalf("explicit malformed username accepted: %s", raw)
		}
	}
	if err := state.Update(func(tx *store.Tx) error {
		return tx.Put("principals", created.ID, session.Human{ID: created.ID})
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := handler.apply(Request{Operation: "human-inspect", Principal: created.ID}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("malformed stored human reported as found: %v", err)
	}
	if err := sessions.ConfigureAdministration(config.BrowserAdministration{BrowserResource: "dashboard", Operators: []string{missingID}}); err != nil {
		t.Fatal(err)
	}
	if _, err := handler.apply(Request{Operation: "human-inspect", Principal: missingID}); !errors.Is(err, ErrAdmin) {
		t.Fatalf("absent configured operator was reported as deletable: %v", err)
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
