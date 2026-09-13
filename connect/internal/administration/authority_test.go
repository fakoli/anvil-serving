package administration

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/device"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

type fixture struct {
	keys                       *access.Keys
	a                          *Authority
	state                      *store.Store
	sessions                   *session.Manager
	issuer                     string
	owner, other, target       session.Human
	ownerSession, otherSession session.Admission
	now                        time.Time
	api                        string
}

func setup(t *testing.T) fixture {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	state, err := store.Open(root, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { state.Close() })
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"issuer": server.URL, "authorization_endpoint": server.URL + "/authorize", "token_endpoint": server.URL + "/token", "jwks_uri": server.URL + "/keys", "id_token_signing_alg_values_supported": []string{"RS256"}})
	}))
	t.Cleanup(server.Close)
	raw, err := os.ReadFile("../../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	declaration, err := config.ReadGateway(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	browserRule := declaration.Resources[0].Rule
	browserRule.ID = "dashboard"
	browserRule.Host = "dash.example.test"
	browserRule.PathPrefix = "/observatory"
	browserRule.Access = "browser"
	browserRule.NativeAuth = "passthrough"
	browserRule.Methods = []string{"GET", "POST"}
	declaration.Resources = append(declaration.Resources, config.Resource{Rule: browserRule, Connector: "dashboard-origin", TunnelAddress: "127.0.0.1:18447"})
	var rules []config.Rule
	api := ""
	browser := ""
	for _, resource := range declaration.Resources {
		if resource.Rule.Access == "browser" {
			rules = append(rules, resource.Rule)
			browser = resource.Rule.ID
		} else {
			api = resource.Rule.ID
		}
	}
	manager, err := session.New(context.Background(), state, rules, session.Config{Issuer: server.URL, ClientID: "test-client", ClientSecret: "synthetic-test-secret", CallbackPath: "/_connect/callback", TransactionLifetime: time.Minute, SessionLifetime: time.Hour, MaxTransactions: 8, MaxPerBrowser: 2, HTTPClient: server.Client()})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(manager.Close)
	owner, err := manager.SetHuman(server.URL, "owner", []string{browser}, false)
	if err != nil {
		t.Fatal(err)
	}
	other, err := manager.SetHuman(server.URL, "other", []string{browser}, false)
	if err != nil {
		t.Fatal(err)
	}
	target, err := manager.SetHuman(server.URL, "target", []string{browser}, false)
	if err != nil {
		t.Fatal(err)
	}
	declaration.DeviceAuthorizations = []config.DeviceAuthorization{{BrowserResource: browser, APIResource: api, Methods: []string{"GET"}, Label: "Terminal test", Principals: map[string]string{owner.ID: "sdk-principal", other.ID: "sdk-principal", target.ID: "sdk-principal"}}}
	apiKeys, err := access.NewKeys(state, []config.Rule{declaration.Resources[0].Rule})
	if err != nil {
		t.Fatal(err)
	}
	if err = apiKeys.SetPrincipal("sdk-principal", []access.Grant{{Resource: api, Methods: []string{"GET"}}}, false); err != nil {
		t.Fatal(err)
	}
	declaration.BrowserAdministration = &config.BrowserAdministration{BrowserResource: browser, Operators: []string{owner.ID, other.ID}}
	authority, err := New(state, manager, declaration)
	if err != nil {
		t.Fatal(err)
	}
	f := fixture{apiKeys, authority, state, manager, server.URL, owner, other, target, session.Admission{}, session.Admission{}, now, api}
	f.ownerSession = f.seedSession(t, owner, strings.Repeat("1", 32))
	f.otherSession = f.seedSession(t, other, strings.Repeat("2", 32))
	return f
}
func (f fixture) seedSession(t *testing.T, human session.Human, id string) session.Admission {
	t.Helper()
	rule := f.a.rules[f.a.settings.BrowserResource]
	var admitted session.Admission
	if err := f.state.Update(func(tx *store.Tx) error {
		value := session.Session{ID: id, Principal: human.ID, PrincipalGeneration: human.Generation, Resource: rule.ID, Host: rule.Host, Generation: 1, Epoch: tx.Epoch(), IssuedAt: f.now.Add(-time.Minute), ExpiresAt: f.now.Add(time.Hour)}
		admitted = session.Admission{SessionID: id, SessionGeneration: 1, Principal: human.ID, PrincipalGeneration: human.Generation, Resource: rule.ID, Host: rule.Host, Epoch: value.Epoch, ExpiresAt: value.ExpiresAt}
		return tx.Put("sessions", "session:"+id, value)
	}); err != nil {
		t.Fatal(err)
	}
	return admitted
}
func request(n int) string { return fmt.Sprintf("00000000-0000-4000-8000-%012x", n) }
func update(human session.Human, browser string, n int) Mutation {
	disabled := true
	return Mutation{Action: "human-update", RequestID: request(n), ExpectedGeneration: fmt.Sprint(human.Generation), Principal: human.ID, Disabled: &disabled, Resources: []string{browser}}
}
func TestMutationRechecksOperatorAndIsIdempotent(t *testing.T) {
	f := setup(t)
	m := update(f.target, f.a.settings.BrowserResource, 1)
	stranger := f.seedSession(t, f.target, strings.Repeat("3", 32))
	if !errors.Is(f.a.Mutate(context.Background(), stranger, m), ErrDenied) {
		t.Fatal("non-operator accepted")
	}
	if err := f.a.Mutate(context.Background(), f.ownerSession, m); err != nil {
		t.Fatal(err)
	}
	if err := f.a.Mutate(context.Background(), f.ownerSession, m); err != nil {
		t.Fatal("idempotent retry", err)
	}
	m.RequestID = request(2)
	if !errors.Is(f.a.Mutate(context.Background(), f.ownerSession, m), ErrConflict) {
		t.Fatal("stale generation accepted")
	}
	var human session.Human
	f.state.View(func(tx *store.Tx) error { return tx.Get("principals", f.target.ID, &human) })
	if !human.Disabled || human.Generation != 2 {
		t.Fatal("mutation was not exactly once")
	}
	m = update(f.other, f.a.settings.BrowserResource, 3)
	if err := f.a.Mutate(context.Background(), f.ownerSession, m); err != nil {
		t.Fatal(err)
	}
	if !errors.Is(f.a.Mutate(context.Background(), f.otherSession, update(f.owner, f.a.settings.BrowserResource, 4)), ErrDenied) {
		t.Fatal("revoked actor accepted")
	}
	if _, err := f.sessions.SetHuman(f.issuer, "owner", []string{f.a.settings.BrowserResource}, true); !errors.Is(err, session.ErrConflict) {
		t.Fatal("local last-operator bypass", err)
	}
}

func TestApplicationRoleMutationInvalidatesSessionsWithoutGatewayOperator(t *testing.T) {
	f := setup(t)
	targetSession := f.seedSession(t, f.target, strings.Repeat("3", 32))
	disabled := false
	mutation := Mutation{Action: "human-update", RequestID: request(20), ExpectedGeneration: fmt.Sprint(f.target.Generation), Principal: f.target.ID, Disabled: &disabled, Resources: []string{f.a.settings.BrowserResource}, ApplicationRoles: map[string]string{f.a.settings.BrowserResource: "admin"}}
	if err := f.a.Mutate(context.Background(), f.ownerSession, mutation); err != nil {
		t.Fatal(err)
	}
	if err := f.sessions.Check(targetSession); err == nil {
		t.Fatal("role change left prior browser session admitted")
	}
	var updated session.Human
	if err := f.state.View(func(tx *store.Tx) error { return tx.Get("principals", f.target.ID, &updated) }); err != nil || updated.ApplicationRoles[f.a.settings.BrowserResource] != "admin" {
		t.Fatalf("role mutation was not stored: %#v, %v", updated, err)
	}
	roleSession := f.seedSession(t, updated, strings.Repeat("4", 32))
	roleSession.ApplicationRole = "admin"
	if err := f.a.Mutate(context.Background(), roleSession, update(updated, f.a.settings.BrowserResource, 21)); !errors.Is(err, ErrDenied) {
		t.Fatal("application admin role granted gateway administration", err)
	}
	inventory, err := f.a.List(context.Background(), f.ownerSession, "users", "", 50)
	if err != nil {
		t.Fatal(err)
	}
	for _, raw := range inventory.Items {
		user := raw.(userItem)
		if user.ID == updated.ID && user.ApplicationRoles[f.a.settings.BrowserResource] != "admin" {
			t.Fatal("role inventory omitted target role")
		}
	}
}
func TestConcurrentCrossDisablePreservesOneOperator(t *testing.T) {
	f := setup(t)
	var wait sync.WaitGroup
	errorsCh := make(chan error, 2)
	for _, pair := range []struct {
		actor  session.Admission
		target session.Human
		n      int
	}{{f.ownerSession, f.other, 1}, {f.otherSession, f.owner, 2}} {
		wait.Add(1)
		go func() {
			defer wait.Done()
			errorsCh <- f.a.Mutate(context.Background(), pair.actor, update(pair.target, f.a.settings.BrowserResource, pair.n))
		}()
	}
	wait.Wait()
	close(errorsCh)
	successes := 0
	for err := range errorsCh {
		if err == nil {
			successes++
		} else if !errors.Is(err, ErrDenied) && !errors.Is(err, ErrConflict) {
			t.Fatal(err)
		}
	}
	if successes != 1 {
		t.Fatal("cross-disable was not serialized", successes)
	}
}
func TestRoleSessionInventoryTracksBrowserAndTerminalValidity(t *testing.T) {
	f := setup(t)
	resource := f.a.settings.BrowserResource
	human, err := f.sessions.SetHuman(f.issuer, "target", []string{resource}, false, map[string]string{resource: "admin"})
	if err != nil {
		t.Fatal(err)
	}
	source := f.seedSession(t, human, strings.Repeat("3", 32))
	source.ApplicationRole = "admin"
	key := f.seedKey(t, human, source, true)
	for _, expected := range []string{"issued", "invalidated"} {
		if expected == "invalidated" {
			if _, err := f.sessions.SetHuman(f.issuer, "target", []string{resource}, false, map[string]string{resource: "member"}); err != nil {
				t.Fatal(err)
			}
		}
		if (f.sessions.Check(source) == nil) != (expected == "issued") {
			t.Fatal("browser admission disagrees with expected inventory status")
		}
		inventory, err := f.a.List(context.Background(), f.ownerSession, "sessions", "", 50)
		if err != nil {
			t.Fatal(err)
		}
		found := 0
		for _, raw := range inventory.Items {
			item := raw.(sessionItem)
			if item.ID == source.SessionID || item.ID == key.ID {
				found++
				if item.Status != expected {
					t.Fatalf("%s session status = %s, want %s", item.Type, item.Status, expected)
				}
			}
		}
		if found != 2 {
			t.Fatal("browser or terminal missing from inventory")
		}
	}
}
func TestRevocationTargetsOneBrowserAndOnlyDeviceKeys(t *testing.T) {
	f := setup(t)
	target := f.seedSession(t, f.target, strings.Repeat("3", 32))
	sibling := f.seedSession(t, f.target, strings.Repeat("4", 32))
	mutation := Mutation{Action: "session-revoke", RequestID: request(1), ExpectedGeneration: "1", SessionType: "browser", SessionID: target.SessionID}
	if err := f.a.Mutate(context.Background(), f.ownerSession, mutation); err != nil {
		t.Fatal(err)
	}
	if f.sessions.Check(target) == nil || f.sessions.Check(sibling) != nil {
		t.Fatal("revocation did not target exactly one session")
	}
	for i, isDevice := range []bool{false, true} {
		key := f.seedKey(t, f.target, sibling, isDevice)
		mutation.RequestID = request(10 + i)
		mutation.SessionType = "terminal"
		mutation.SessionID = key.ID
		err := f.a.Mutate(context.Background(), f.ownerSession, mutation)
		if isDevice && err != nil {
			t.Fatal(err)
		}
		if !isDevice && !errors.Is(err, ErrDenied) {
			t.Fatal("manual key revoked")
		}
	}
}
func TestCancelledAndMalformedMutationCannotWrite(t *testing.T) {
	f := setup(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	m := update(f.target, f.a.settings.BrowserResource, 1)
	if !errors.Is(f.a.Mutate(ctx, f.ownerSession, m), ErrUnavailable) {
		t.Fatal("cancelled write accepted")
	}
	m.ExpectedGeneration = "01"
	if !errors.Is(f.a.Mutate(context.Background(), f.ownerSession, m), ErrInvalid) {
		t.Fatal("noncanonical generation accepted")
	}
}
func TestInventoryPaginationRedactionAndNamespaceCollision(t *testing.T) {
	f := setup(t)
	id := f.ownerSession.SessionID
	key := f.seedKey(t, f.owner, f.ownerSession, true)
	if err := f.state.Update(func(tx *store.Tx) error {
		if err := tx.Delete("api_keys", key.ID); err != nil {
			return err
		}
		key.ID = id
		return tx.Put("api_keys", id, key)
	}); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"users", "sessions"} {
		cursor := ""
		seen := map[string]bool{}
		for page := 0; page < 10; page++ {
			result, err := f.a.List(context.Background(), f.ownerSession, kind, cursor, 1)
			if err != nil {
				t.Fatal(err)
			}
			encoded, _ := json.Marshal(result)
			for _, secret := range []string{"digest", "sdk-principal", "mapping_hash", "cookie", "device_code"} {
				if strings.Contains(string(encoded), secret) {
					t.Fatal("inventory leaked", secret)
				}
			}
			for _, item := range result.Items {
				key := ""
				switch value := item.(type) {
				case userItem:
					key = value.ID
				case sessionItem:
					key = value.Type + ":" + value.ID
				}
				if seen[key] {
					t.Fatal("duplicate entry", key)
				}
				seen[key] = true
			}
			if result.NextCursor == nil {
				break
			}
			cursor = *result.NextCursor
		}
		if len(seen) != 3 {
			t.Fatal("incomplete inventory", kind, len(seen))
		}
	}
	if _, err := f.a.List(context.Background(), f.ownerSession, "users", "human:raw", 1); !errors.Is(err, ErrInvalid) {
		t.Fatal("bad cursor accepted")
	}
	denied := f.seedSession(t, f.target, strings.Repeat("3", 32))
	if _, err := f.a.List(context.Background(), denied, "users", "", 50); !errors.Is(err, ErrDenied) {
		t.Fatal("unprivileged inventory")
	}
}

func (f fixture) seedKey(t *testing.T, human session.Human, source session.Admission, isDevice bool) access.Key {
	t.Helper()
	grants := []access.Grant{{Resource: f.api, Methods: []string{"GET"}}}
	var key access.Key
	var err error
	if isDevice {
		hash := ""
		for candidate := range f.a.bindings {
			hash = candidate
		}
		_, key, err = f.keys.IssueDevice("sdk-principal", grants, 30*time.Minute, source.ExpiresAt, access.DeviceCredential{HumanID: human.ID, HumanGeneration: human.Generation, MappingHash: hash, Session: source.SessionID, SessionGeneration: source.SessionGeneration})
	} else {
		_, key, err = f.keys.Issue("sdk-principal", grants, time.Hour)
	}
	if err != nil {
		t.Fatal(err)
	}
	return key
}

func TestDeviceInventoryRechecksAllAuthorityFences(t *testing.T) {
	for _, kind := range []string{"source-revoke", "source-generation", "human-generation", "human-disabled", "api-generation", "api-disabled", "api-grants", "mapping", "epoch"} {
		t.Run(kind, func(t *testing.T) {
			f := setup(t)
			source := f.seedSession(t, f.target, strings.Repeat("3", 32))
			key := f.seedKey(t, f.target, source, true)
			getStatus := func() string {
				result, err := f.a.List(context.Background(), f.ownerSession, "sessions", "", 50)
				if err != nil {
					t.Fatal(err)
				}
				for _, item := range result.Items {
					row := item.(sessionItem)
					if row.Type == "terminal" && row.ID == key.ID {
						return row.Status
					}
				}
				t.Fatal("terminal missing")
				return ""
			}
			if getStatus() != "issued" {
				t.Fatal("new credential not issued")
			}
			if kind == "mapping" {
				f.a.bindings = map[string]device.Binding{}
			} else if kind == "epoch" {
				// Change only the credential epoch so the viewing administrator remains valid.
				f.state.Update(func(tx *store.Tx) error { key.Epoch = strings.Repeat("a", 64); return tx.Put("api_keys", key.ID, key) })
			} else {
				f.state.Update(func(tx *store.Tx) error {
					switch kind {
					case "source-revoke", "source-generation":
						var current session.Session
						if err := tx.Get("sessions", "session:"+source.SessionID, &current); err != nil {
							return err
						}
						if kind == "source-revoke" {
							current.Revoked = true
						} else {
							current.Generation++
						}
						return tx.Put("sessions", "session:"+current.ID, current)
					case "human-generation", "human-disabled":
						var human session.Human
						if err := tx.Get("principals", f.target.ID, &human); err != nil {
							return err
						}
						if kind == "human-disabled" {
							human.Disabled = true
						} else {
							human.Generation++
						}
						return tx.Put("principals", human.ID, human)
					default:
						var principal access.Principal
						if err := tx.Get("principals", "sdk-principal", &principal); err != nil {
							return err
						}
						if kind == "api-generation" {
							principal.Generation++
						} else if kind == "api-disabled" {
							principal.Disabled = true
						} else {
							principal.Grants = nil
						}
						return tx.Put("principals", principal.ID, principal)
					}
				})
			}
			if getStatus() != "invalidated" {
				t.Fatal("stale device credential not invalidated", kind)
			}
		})
	}
}
