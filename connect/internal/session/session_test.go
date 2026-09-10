package session

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/go-jose/go-jose/v4"
	"golang.org/x/oauth2"
)

type tokenClaims struct {
	Issuer          string
	Audience        any
	Subject         string
	Nonce           string
	Expires         time.Time
	IssuedAt        time.Time
	AuthorizedParty string
	NotBefore       *int64
	BadSignature    bool
}

// syntheticIDP is deliberately only an in-process protocol fixture. It is not
// an Authelia/browser acceptance test; that belongs to the later browser gate.
type syntheticIDP struct {
	t         *testing.T
	server    *httptest.Server
	key       *rsa.PrivateKey
	mu        sync.Mutex
	claims    tokenClaims
	challenge string
	now       func() time.Time
	onToken   func()
}

func newSyntheticIDP(t *testing.T) *syntheticIDP {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	fake := &syntheticIDP{t: t, key: key}
	fake.server = httptest.NewTLSServer(http.HandlerFunc(fake.serveHTTP))
	t.Cleanup(fake.server.Close)
	return fake
}

func (f *syntheticIDP) issuer() string { return f.server.URL }

func (f *syntheticIDP) serveHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/.well-known/openid-configuration":
		json.NewEncoder(w).Encode(map[string]any{
			"issuer": f.issuer(), "authorization_endpoint": f.issuer() + "/authorize",
			"token_endpoint": f.issuer() + "/token", "jwks_uri": f.issuer() + "/keys",
			"id_token_signing_alg_values_supported": []string{"RS256"},
		})
	case "/keys":
		json.NewEncoder(w).Encode(map[string]any{"keys": []jose.JSONWebKey{{Key: &f.key.PublicKey, KeyID: "fixture", Algorithm: "RS256", Use: "sig"}}})
	case "/token":
		if err := r.ParseForm(); err != nil {
			http.Error(w, "bad form", http.StatusBadRequest)
			return
		}
		f.mu.Lock()
		claims, challenge, hook := f.claims, f.challenge, f.onToken
		f.onToken = nil
		f.mu.Unlock()
		clientID, clientSecret, basic := r.BasicAuth()
		if !basic || clientID != "connect-browser" || clientSecret != "fixture-secret" || r.Form.Get("grant_type") != "authorization_code" || r.Form.Get("code") != "code" || r.Form.Get("redirect_uri") != "https://dash.example.test/_connect/callback" || r.Form.Get("code_verifier") == "" || oauth2.S256ChallengeFromVerifier(r.Form.Get("code_verifier")) != challenge {
			http.Error(w, "exchange rejected", http.StatusBadRequest)
			return
		}
		if hook != nil {
			hook()
		}
		raw, err := f.sign(claims)
		if err != nil {
			f.t.Error(err)
			http.Error(w, "sign failed", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"access_token": "idp-access-token-must-not-persist", "token_type": "Bearer", "expires_in": 3600, "id_token": raw})
	default:
		http.NotFound(w, r)
	}
}

func (f *syntheticIDP) sign(claims tokenClaims) (string, error) {
	clock := time.Now
	if f.now != nil {
		clock = f.now
	}
	if claims.Expires.IsZero() {
		claims.Expires = clock().Add(time.Hour)
	}
	key := f.key
	if claims.BadSignature {
		var err error
		key, err = rsa.GenerateKey(rand.Reader, 2048)
		if err != nil {
			return "", err
		}
	}
	signer, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.RS256, Key: key}, (&jose.SignerOptions{}).WithHeader("kid", "fixture").WithType("JWT"))
	if err != nil {
		return "", err
	}
	issued := claims.IssuedAt
	if issued.IsZero() {
		issued = clock().Add(-time.Minute)
	}
	payload := map[string]any{"iss": claims.Issuer, "aud": claims.Audience, "sub": claims.Subject, "nonce": claims.Nonce, "iat": issued.Unix(), "exp": claims.Expires.Unix()}
	if claims.AuthorizedParty != "" {
		payload["azp"] = claims.AuthorizedParty
	}
	if claims.NotBefore != nil {
		payload["nbf"] = *claims.NotBefore
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	object, err := signer.Sign(encoded)
	if err != nil {
		return "", err
	}
	return object.CompactSerialize()
}

func sessionRules() []config.Rule {
	limits := config.Limits{RequestBytes: 1024, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 1}
	return []config.Rule{
		{ID: "dash", Host: "dash.example.test", PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none", Limits: limits},
		{ID: "other", Host: "other.example.test", PathPrefix: "/", Methods: []string{"GET"}, Access: "browser", NativeAuth: "none", Limits: limits},
	}
}

func sessionFixture(t *testing.T, maximum, perBrowser int) (*Manager, *store.Store, *syntheticIDP, *time.Time, string) {
	t.Helper()
	now := time.Date(2026, 9, 9, 12, 0, 0, 0, time.UTC)
	directory := filepath.Join(t.TempDir(), "authority")
	state, err := store.Open(directory, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = state.Close() })
	idp := newSyntheticIDP(t)
	idp.now = func() time.Time { return now }
	manager, err := New(context.Background(), state, sessionRules(), Config{
		Issuer: idp.issuer(), ClientID: "connect-browser", ClientSecret: "fixture-secret", CallbackPath: "/_connect/callback",
		TransactionLifetime: DefaultTransactionLifetime, SessionLifetime: time.Hour, MaxTransactions: maximum, MaxPerBrowser: perBrowser, HTTPClient: idp.server.Client(),
	})
	if err != nil {
		t.Fatal(err)
	}
	return manager, state, idp, &now, directory
}

func challengeValues(t *testing.T, challenge Challenge) url.Values {
	t.Helper()
	u, err := url.Parse(challenge.AuthorizationURL)
	if err != nil {
		t.Fatal(err)
	}
	return u.Query()
}

func complete(t *testing.T, manager *Manager, idp *syntheticIDP, challenge Challenge, subject string) Completion {
	t.Helper()
	query := challengeValues(t, challenge)
	idp.mu.Lock()
	idp.challenge = query.Get("code_challenge")
	idp.claims = tokenClaims{Issuer: idp.issuer(), Audience: "connect-browser", Subject: subject, Nonce: query.Get("nonce")}
	idp.mu.Unlock()
	result, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: challenge.Binding})
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func configureToken(idp *syntheticIDP, challenge Challenge, claims tokenClaims) url.Values {
	u, _ := url.Parse(challenge.AuthorizationURL)
	values := u.Query()
	if claims.Issuer == "" {
		claims.Issuer = idp.issuer()
	}
	if claims.Audience == nil {
		claims.Audience = "connect-browser"
	}
	if claims.Nonce == "" {
		claims.Nonce = values.Get("nonce")
	}
	idp.mu.Lock()
	idp.challenge, idp.claims = values.Get("code_challenge"), claims
	idp.mu.Unlock()
	return values
}

func TestOIDCBrowserSessionLifecycle(t *testing.T) {
	manager, state, idp, now, directory := sessionFixture(t, 8, 2)
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash", "other"}, false); err != nil {
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
	query := challengeValues(t, challenge)
	if query.Get("redirect_uri") != "https://dash.example.test/_connect/callback" || query.Get("response_type") != "code" || query.Get("scope") != "openid" || query.Get("state") == "" || query.Get("nonce") == "" || query.Get("code_challenge_method") != "S256" || query.Get("code_challenge") == "" {
		t.Fatalf("incomplete OIDC authorization request: %v", query)
	}
	result := complete(t, manager, idp, challenge, "person-1")
	if result.Host != "dash.example.test" || result.Resource != "dash" || result.ReturnPath != "/report" || !result.ExpiresAt.Equal(now.Add(time.Hour)) {
		t.Fatalf("unexpected completion: %+v", result)
	}
	admission, err := manager.Authenticate(result.Cookie, "dash.example.test")
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.Check(admission); err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{result.Cookie + "x", strings.Replace(result.Cookie, "acs1.", "ac1.", 1)} {
		if _, err := manager.Authenticate(value, "dash.example.test"); err == nil {
			t.Fatal("fixed or malformed session accepted")
		}
	}
	if _, err := manager.Authenticate(result.Cookie, "other.example.test"); err == nil {
		t.Fatal("host-only session was accepted on sibling resource")
	}
	if err := state.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := os.ReadFile(filepath.Join(directory, "state.db"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(database, []byte("idp-access-token-must-not-persist")) || bytes.Contains(database, []byte("person-1")) {
		t.Fatal("IdP credential or subject persisted in Connect authority state")
	}
	state, err = store.Open(directory, func() time.Time { return *now })
	if err != nil {
		t.Fatal(err)
	}
	defer state.Close()
	manager.state = state
	*now = now.Add(time.Hour)
	if _, err := manager.Authenticate(result.Cookie, "dash.example.test"); err == nil {
		t.Fatal("expired session accepted")
	}
	if err := state.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if err := manager.Check(admission); err == nil {
		t.Fatal("epoch did not invalidate admission")
	}
}

func TestOIDCFailuresAndReplayFailClosed(t *testing.T) {
	manager, _, idp, now, _ := sessionFixture(t, 8, 2)
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(*tokenClaims){
		"issuer":         func(c *tokenClaims) { c.Issuer = "https://wrong.example.test" },
		"audience":       func(c *tokenClaims) { c.Audience = "other-client" },
		"multi-audience": func(c *tokenClaims) { c.Audience = []string{"connect-browser", "other-client"} },
		"azp":            func(c *tokenClaims) { c.AuthorizedParty = "other-client" },
		"nonce":          func(c *tokenClaims) { c.Nonce = "wrong" },
		"expiry":         func(c *tokenClaims) { c.Expires = now.Add(-time.Minute) },
		"expiry-at-now":  func(c *tokenClaims) { c.Expires = *now },
		"future-iat":     func(c *tokenClaims) { c.IssuedAt = now.Add(time.Second) },
		"future-nbf": func(c *tokenClaims) {
			value := now.Add(time.Second).Unix()
			c.NotBefore = &value
		},
		"signature": func(c *tokenClaims) { c.BadSignature = true },
	} {
		t.Run(name, func(t *testing.T) {
			binding, _ := NewBinding()
			challenge, err := manager.Begin("dash", "/", binding)
			if err != nil {
				t.Fatal(err)
			}
			query := challengeValues(t, challenge)
			claims := tokenClaims{Issuer: idp.issuer(), Audience: "connect-browser", Subject: "person-1", Nonce: query.Get("nonce")}
			mutate(&claims)
			idp.mu.Lock()
			idp.challenge, idp.claims = query.Get("code_challenge"), claims
			idp.mu.Unlock()
			if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: binding}); err == nil {
				t.Fatal("invalid OIDC assertion accepted")
			}
		})
	}
	binding, _ := NewBinding()
	challenge, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	query := challengeValues(t, challenge)
	if _, err := manager.Complete(context.Background(), Callback{Host: "other.example.test", State: query.Get("state"), Code: "code", Binding: binding}); err == nil {
		t.Fatal("sibling callback host consumed transaction")
	}
	if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: "wrong"}); err == nil {
		t.Fatal("wrong binding consumed transaction")
	}
	complete(t, manager, idp, challenge, "person-1")
	if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: binding}); err == nil {
		t.Fatal("callback replay accepted")
	}
	for _, path := range []string{"https://evil.example/", "//evil.example/", "/../admin", "/%2fadmin", "/report?next=https://evil.example/"} {
		if _, err := manager.Begin("dash", path, binding); err == nil {
			t.Fatalf("redirect escape accepted: %q", path)
		}
	}
	if _, err := manager.Begin("dash", "/", ""); err == nil {
		t.Fatal("missing binding accepted")
	}
	if _, err := manager.Begin("missing", "/", binding); err == nil {
		t.Fatal("unknown resource accepted")
	}
	if _, err := manager.Begin("dash", "/", "not-a-canonical-256-bit-binding"); err == nil {
		t.Fatal("noncanonical transaction binding accepted")
	}
}

func TestPKCEStateAndConcurrentCallbackRedemption(t *testing.T) {
	manager, state, idp, _, _ := sessionFixture(t, 8, 2)
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	binding, _ := NewBinding()
	challenge, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	query := configureToken(idp, challenge, tokenClaims{Subject: "person-1"})
	if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: "wrong-state", Code: "code", Binding: binding}); err == nil {
		t.Fatal("unknown callback state accepted")
	}
	if err := state.Update(func(tx *store.Tx) error {
		var record transaction
		key := transactionKey(query.Get("state"))
		if err := tx.Get("transactions", key, &record); err != nil {
			return err
		}
		record.PKCEVerifier = "wrong"
		return tx.Put("transactions", key, record)
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: binding}); err == nil {
		t.Fatal("failed PKCE exchange accepted")
	}

	binding, _ = NewBinding()
	challenge, err = manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	query = configureToken(idp, challenge, tokenClaims{Subject: "person-1"})
	callback := Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: binding}
	start := make(chan struct{})
	results := make(chan error, 2)
	for range 2 {
		go func() {
			<-start
			_, err := manager.Complete(context.Background(), callback)
			results <- err
		}()
	}
	close(start)
	first, second := <-results, <-results
	if (first == nil) == (second == nil) {
		t.Fatalf("callback redemption was not single-use: %v, %v", first, second)
	}
}

func TestTransactionEpochAndClockRollback(t *testing.T) {
	manager, state, idp, now, _ := sessionFixture(t, 8, 2)
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	binding, _ := NewBinding()
	pending, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	stateValue := challengeValues(t, pending).Get("state")
	if err := state.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: stateValue, Code: "code", Binding: binding}); err == nil {
		t.Fatal("pre-reset transaction minted a current-epoch session")
	}
	binding, _ = NewBinding()
	rollback, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	*now = now.Add(-time.Second)
	if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: challengeValues(t, rollback).Get("state"), Code: "code", Binding: binding}); err == nil {
		t.Fatal("clock rollback accepted a pending transaction")
	}
	*now = now.Add(time.Second)
	binding, _ = NewBinding()
	duringExchange, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	query := configureToken(idp, duringExchange, tokenClaims{Subject: "person-1"})
	idp.mu.Lock()
	idp.onToken = func() { _ = state.ResetAuthority() }
	idp.mu.Unlock()
	if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: binding}); err == nil {
		t.Fatal("reset during OIDC exchange minted a session")
	}
	binding, _ = NewBinding()
	fresh, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	if result := complete(t, manager, idp, fresh, "person-1"); result.Cookie == "" {
		t.Fatal("fresh current-epoch transaction did not mint a session")
	}
}

func TestTransactionBoundsHumanGenerationAndLogout(t *testing.T) {
	manager, _, idp, now, _ := sessionFixture(t, 2, 1)
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash", "other"}, false); err != nil {
		t.Fatal(err)
	}
	firstBinding, _ := NewBinding()
	_, err := manager.Begin("dash", "/", firstBinding)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Begin("dash", "/", firstBinding); err == nil {
		t.Fatal("per-browser transaction cap ignored")
	}
	secondBinding, _ := NewBinding()
	if _, err := manager.Begin("dash", "/", secondBinding); err != nil {
		t.Fatal(err)
	}
	thirdBinding, _ := NewBinding()
	if _, err := manager.Begin("dash", "/", thirdBinding); err == nil {
		t.Fatal("global transaction cap ignored")
	}
	// Simulate a stricter reload after three rows exist. Sweeping must still use
	// the absolute store bound rather than the newly reduced policy cap.
	manager.maxTransactions = 1
	*now = now.Add(DefaultTransactionLifetime)
	fresh, err := manager.Begin("dash", "/", thirdBinding)
	if err != nil {
		t.Fatal("expired transactions were not reclaimed")
	}
	// Complete a fresh dash session then change principal generation: no stored
	// session can survive a policy change or a subsequent global logout.
	result := complete(t, manager, idp, fresh, "person-1")
	if _, err := manager.Authenticate(result.Cookie, "dash.example.test"); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash", "other"}, false); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Authenticate(result.Cookie, "dash.example.test"); err == nil {
		t.Fatal("principal generation did not invalidate session")
	}
	challengeBinding, _ := NewBinding()
	challenge, err := manager.Begin("dash", "/", challengeBinding)
	if err != nil {
		t.Fatal(err)
	}
	current := complete(t, manager, idp, challenge, "person-1")
	if err := manager.Logout(current.Cookie, "dash.example.test"); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Authenticate(current.Cookie, "dash.example.test"); err == nil {
		t.Fatal("logout did not revoke Connect session")
	}
	if _, err := manager.SetHuman(idp.issuer(), "unprovisioned", []string{"dash"}, true); err != nil {
		t.Fatal(err)
	}
}

func TestOwnedOIDCClientBoundsAndOrigin(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/redirect":
			http.Redirect(w, r, "https://other.example.test/", http.StatusFound)
		case "/large":
			_, _ = w.Write([]byte(strings.Repeat("x", maxOIDCResponseBytes+1)))
		case "/large-header":
			w.Header().Set("X-Large", strings.Repeat("x", maxOIDCHeaderBytes+1))
			_, _ = w.Write([]byte("ok"))
		default:
			_, _ = w.Write([]byte("ok"))
		}
	}))
	defer server.Close()
	issuer, err := validatedIssuer(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	client, err := ownedOIDCClient(server.Client(), issuer)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseIdleConnections()
	for _, target := range []string{"http://example.test/", "https://other.example.test/"} {
		if _, err := client.Get(target); err == nil {
			t.Fatalf("non-issuer OIDC request accepted: %s", target)
		}
	}
	response, err := client.Get(server.URL + "/redirect")
	if err != nil || response.StatusCode != http.StatusFound {
		if response != nil {
			response.Body.Close()
		}
		t.Fatalf("redirect was followed or rejected incorrectly: %v", err)
	}
	response.Body.Close()
	response, err = client.Get(server.URL + "/large")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.ReadAll(response.Body); err == nil {
		response.Body.Close()
		t.Fatal("oversized OIDC body accepted")
	}
	response.Body.Close()
	if _, err := client.Get(server.URL + "/large-header"); err == nil {
		t.Fatal("oversized OIDC response header accepted")
	}
	transport := server.Client().Transport.(*http.Transport).Clone()
	transport.TLSClientConfig = transport.TLSClientConfig.Clone()
	transport.TLSClientConfig.InsecureSkipVerify = true
	if _, err := ownedOIDCClient(&http.Client{Transport: transport}, issuer); err == nil {
		t.Fatal("insecure TLS OIDC transport accepted")
	}
	for _, value := range []string{"https://issuer.example.test?", "https://issuer.example.test#"} {
		if _, err := validatedIssuer(value); err == nil {
			t.Fatalf("noncanonical issuer accepted: %q", value)
		}
	}
	if sameIssuerOrigin(issuer, server.URL+"/token?") || sameIssuerOrigin(issuer, server.URL+"/token#") {
		t.Fatal("noncanonical discovered endpoint accepted")
	}
}

func TestUnprovisionedOIDCIdentityIsDenied(t *testing.T) {
	manager, _, idp, _, _ := sessionFixture(t, 8, 2)
	binding, _ := NewBinding()
	challenge, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	query := challengeValues(t, challenge)
	idp.mu.Lock()
	idp.challenge = query.Get("code_challenge")
	idp.claims = tokenClaims{Issuer: idp.issuer(), Audience: "connect-browser", Subject: "not-provisioned", Nonce: query.Get("nonce")}
	idp.mu.Unlock()
	if _, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: binding}); err == nil {
		t.Fatal("valid but unprovisioned OIDC identity accepted")
	}
}

func TestCheckDeviceSessionFailsClosed(t *testing.T) {
	manager, _, idp, now, _ := sessionFixture(t, 8, 2)
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	binding, err := NewBinding()
	if err != nil {
		t.Fatal(err)
	}
	completed := complete(t, manager, idp, mustBegin(t, manager, binding, "dash"), "person-1")
	admitted, err := manager.Authenticate(completed.Cookie, "dash.example.test")
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.CheckDeviceSession(admitted.SessionID, admitted.SessionGeneration, admitted.Principal, admitted.PrincipalGeneration); err != nil {
		t.Fatal("valid session rejected", err)
	}
	rule := manager.rules["dash"]
	drifted := rule
	drifted.Host = "other.example.test"
	manager.rules["dash"] = drifted
	if err := manager.CheckDeviceSession(admitted.SessionID, admitted.SessionGeneration, admitted.Principal, admitted.PrincipalGeneration); err == nil {
		t.Fatal("browser host drift accepted")
	}
	manager.rules["dash"] = rule
	for _, changed := range []Admission{
		{SessionID: strings.Repeat("0", 32), SessionGeneration: admitted.SessionGeneration, Principal: admitted.Principal, PrincipalGeneration: admitted.PrincipalGeneration},
		{SessionID: admitted.SessionID, SessionGeneration: admitted.SessionGeneration + 1, Principal: admitted.Principal, PrincipalGeneration: admitted.PrincipalGeneration},
		{SessionID: admitted.SessionID, SessionGeneration: admitted.SessionGeneration, Principal: "human:" + strings.Repeat("b", 64), PrincipalGeneration: admitted.PrincipalGeneration},
	} {
		if err := manager.CheckDeviceSession(changed.SessionID, changed.SessionGeneration, changed.Principal, changed.PrincipalGeneration); err == nil {
			t.Fatal("wrong device session fence accepted")
		}
	}
	*now = admitted.ExpiresAt
	if err := manager.CheckDeviceSession(admitted.SessionID, admitted.SessionGeneration, admitted.Principal, admitted.PrincipalGeneration); err == nil {
		t.Fatal("expired device session accepted")
	}
	*now = admitted.ExpiresAt.Add(-time.Second)
	if err := manager.Logout(completed.Cookie, "dash.example.test"); err != nil {
		t.Fatal(err)
	}
	if err := manager.CheckDeviceSession(admitted.SessionID, admitted.SessionGeneration, admitted.Principal, admitted.PrincipalGeneration); err == nil {
		t.Fatal("logged-out device session accepted")
	}
}

func mustBegin(t *testing.T, manager *Manager, binding, resource string) Challenge {
	t.Helper()
	challenge, err := manager.Begin(resource, "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	return challenge
}
