package session

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func TestAdvertisedResponseIssuerRequiredBeforeCodeExchange(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	idp := &syntheticIDP{t: t, key: key}
	idp.server = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/.well-known/openid-configuration" {
			_ = json.NewEncoder(w).Encode(map[string]any{"issuer": idp.issuer(), "authorization_endpoint": idp.issuer() + "/authorize", "token_endpoint": idp.issuer() + "/token", "jwks_uri": idp.issuer() + "/keys", "id_token_signing_alg_values_supported": []string{"RS256"}, "authorization_response_iss_parameter_supported": true})
			return
		}
		idp.serveHTTP(w, r)
	}))
	defer idp.server.Close()
	state, err := store.Open(filepath.Join(t.TempDir(), "state"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer state.Close()
	manager, err := New(context.Background(), state, sessionRules(), Config{Issuer: idp.issuer(), ClientID: "connect-browser", ClientSecret: "fixture-secret", CallbackPath: "/_connect/callback", TransactionLifetime: time.Minute, SessionLifetime: time.Hour, MaxTransactions: 8, MaxPerBrowser: 2, HTTPClient: idp.server.Client()})
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close()
	if _, err := manager.SetHuman(idp.issuer(), "person", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	binding, err := NewBinding()
	if err != nil {
		t.Fatal(err)
	}
	challenge, err := manager.Begin("dash", "/report", binding)
	if err != nil {
		t.Fatal(err)
	}
	query := configureToken(idp, challenge, tokenClaims{Subject: "person"})
	var exchanges atomic.Int32
	idp.mu.Lock()
	idp.onToken = func() { exchanges.Add(1) }
	idp.mu.Unlock()
	callback := Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: binding}
	for _, issuer := range []string{"", "https://other.example.test", idp.issuer() + "/", idp.issuer() + "?a=b"} {
		callback.Issuer = issuer
		if _, err := manager.Complete(context.Background(), callback); err == nil {
			t.Fatal("missing or substituted issuer accepted")
		}
		if exchanges.Load() != 0 {
			t.Fatal("code sent before issuer validated")
		}
	}
	callback.Issuer = idp.issuer()
	if _, err := manager.Complete(context.Background(), callback); err != nil {
		t.Fatal("issuer rejection consumed legitimate transaction", err)
	}
	if exchanges.Load() != 1 {
		t.Fatal("valid issuer did not exchange exactly once")
	}
}
