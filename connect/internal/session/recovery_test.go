package session

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func TestRestoredHistoricalGrantCannotMintSessionOrAPIKey(t *testing.T) {
	manager, state, idp, now, _ := sessionFixture(t, 8, 2)
	defer manager.Close()
	if _, err := manager.SetHuman(idp.issuer(), "person", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	binding, _ := NewBinding()
	challenge, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	oldSession := complete(t, manager, idp, challenge, "person")
	rule := config.Rule{ID: "router", Host: "api.example.test", PathPrefix: "/v1", Methods: []string{"GET"}, Access: "api", NativeAuth: "delegate-bearer", Limits: config.Limits{RequestBytes: 1024, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 1}}
	keys, err := access.NewKeys(state, []config.Rule{rule})
	if err != nil {
		t.Fatal(err)
	}
	grants := []access.Grant{{Resource: "router", Methods: []string{"GET"}}}
	if err := keys.SetPrincipal("sdk", grants, false); err != nil {
		t.Fatal(err)
	}
	oldKey, _, err := keys.Issue("sdk", grants, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	// Snapshot predates revocation, the exact case that must never revive access.
	data, err := state.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.SetHuman(idp.issuer(), "person", []string{"dash"}, true); err != nil {
		t.Fatal(err)
	}
	if err := keys.SetPrincipal("sdk", grants, true); err != nil {
		t.Fatal(err)
	}
	recovered, err := store.Open(filepath.Join(t.TempDir(), "recovered"), func() time.Time { return *now })
	if err != nil {
		t.Fatal(err)
	}
	defer recovered.Close()
	if err := recovered.RestoreSnapshot(data); err != nil {
		t.Fatal(err)
	}
	next, err := New(context.Background(), recovered, sessionRules(), Config{Issuer: idp.issuer(), ClientID: "connect-browser", ClientSecret: "fixture-secret", CallbackPath: "/_connect/callback", TransactionLifetime: time.Minute, SessionLifetime: time.Hour, MaxTransactions: 8, MaxPerBrowser: 2, HTTPClient: idp.server.Client()})
	if err != nil {
		t.Fatal(err)
	}
	defer next.Close()
	if _, err := next.Authenticate(oldSession.Cookie, oldSession.Host); err == nil {
		t.Fatal("historical browser cookie revived")
	}
	nextKeys, err := access.NewKeys(recovered, []config.Rule{rule})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := nextKeys.Authenticate(oldKey, "router", "GET"); err == nil {
		t.Fatal("historical API key revived")
	}
	if _, _, err := nextKeys.Issue("sdk", grants, time.Hour); err == nil {
		t.Fatal("historical API grant allowed new issuance")
	}
	binding, _ = NewBinding()
	challenge, err = next.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	query := configureToken(idp, challenge, tokenClaims{Subject: "person"})
	if _, err := next.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: binding}); err == nil {
		t.Fatal("fresh valid OIDC login revived historical human grant")
	}
	// The owner can explicitly approve new access after reviewing recovered scope.
	if _, err := next.SetHuman(idp.issuer(), "person", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	binding, _ = NewBinding()
	challenge, err = next.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	newSession := complete(t, next, idp, challenge, "person")
	if _, err := next.Authenticate(newSession.Cookie, newSession.Host); err != nil {
		t.Fatal("new owner grant unusable", err)
	}
}
