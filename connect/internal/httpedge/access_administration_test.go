package httpedge

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/administration"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

type accessAuthorityStub struct {
	inventory administration.Inventory
	listErr   error
	mutateErr error
	listCalls int
	mutations []administration.Mutation
	admitted  session.Admission
}

func (s *accessAuthorityStub) List(ctx context.Context, admitted session.Admission, kind, cursor string, limit int) (administration.Inventory, error) {
	if ctx.Err() != nil || admitted != s.admitted || kind != s.inventory.Kind || limit < 1 || limit > 50 {
		return administration.Inventory{}, administration.ErrDenied
	}
	s.listCalls++
	if s.listErr != nil {
		return administration.Inventory{}, s.listErr
	}
	return s.inventory, nil
}

func (s *accessAuthorityStub) Mutate(ctx context.Context, admitted session.Admission, mutation administration.Mutation) error {
	if ctx.Err() != nil || admitted != s.admitted {
		return administration.ErrDenied
	}
	if s.mutateErr != nil {
		return s.mutateErr
	}
	s.mutations = append(s.mutations, mutation)
	return nil
}

func administrationDeclaration() config.Gateway {
	declaration := browserDeclaration("none")
	declaration.BrowserAdministration = &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{"human:" + strings.Repeat("a", 64)}}
	return declaration
}

func administrationAdmission() session.Admission {
	return session.Admission{SessionID: strings.Repeat("1", 32), SessionGeneration: 1, Principal: "human:" + strings.Repeat("a", 64), PrincipalGeneration: 1, Resource: "dash", Host: "dash.example.test", Epoch: strings.Repeat("b", 64), ExpiresAt: time.Now().Add(time.Minute).UTC()}
}

func administrationBrowser(t *testing.T, authority *accessAuthorityStub) *Browser {
	t.Helper()
	declaration := administrationDeclaration()
	browserAuthority := newBrowserAuthorityStub()
	browserAuthority.admitted = authority.admitted
	browser, err := NewBrowser(declaration, browserAuthority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {
		t.Error("Access endpoint reached origin dispatch")
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	handler, err := NewAccessAdministration(declaration, authority)
	if err != nil || browser.SetAccessAdministration(handler) != nil {
		t.Fatal("Access handler was not attached", err)
	}
	return browser
}

func administrationUsers() administration.Inventory {
	return administration.Inventory{Kind: "users", Items: []any{map[string]any{
		"id":                "human:" + strings.Repeat("a", 64),
		"generation":        "1",
		"disabled":          false,
		"resources":         []string{"dash"},
		"application_roles": map[string]string{"dash": "admin"},
		"administrator":     true,
	}}}
}

func accessRequest(method, target, body string) *http.Request {
	r := browserRequest(method, target, strings.NewReader(body))
	r.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	return r
}

func decodeAccessResponse(t *testing.T, response *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	if response.Header().Get("Cache-Control") != "no-store" || response.Header().Get("Content-Type") != "application/json" {
		t.Fatal("Access response was not a non-cacheable JSON envelope")
	}
	var value map[string]any
	if json.Unmarshal(response.Body.Bytes(), &value) != nil || value["schema"] != accessSchema {
		t.Fatal("Access response was not a fixed schema envelope")
	}
	return value
}

func TestAccessAdministrationListAndMutationAreSameOriginBound(t *testing.T) {
	authority := &accessAuthorityStub{admitted: administrationAdmission(), inventory: administrationUsers()}
	browser := administrationBrowser(t, authority)
	get := accessRequest(http.MethodGet, accessAdministrationPath+"?kind=users&limit=50", "")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, get)
	if response.Code != http.StatusOK || authority.listCalls != 1 {
		t.Fatal("configured operator could not read the bounded inventory", response.Code)
	}
	value := decodeAccessResponse(t, response)
	items := value["items"].([]any)
	if items[0].(map[string]any)["application_roles"].(map[string]any)["dash"] != "admin" {
		t.Fatal("role inventory was not preserved")
	}
	csrf, ok := value["csrf"].(string)
	if !ok || len(csrf) != 43 || value["current_principal"] != authority.admitted.Principal || value["current_session"] != authority.admitted.SessionID {
		t.Fatal("Access inventory did not bind its CSRF response to the browser admission")
	}
	body, err := json.Marshal(map[string]any{
		"action": "human-update", "request_id": "00000000-0000-4000-8000-000000000001", "expected_generation": "1", "csrf": csrf,
		"principal": authority.admitted.Principal, "disabled": false, "resources": []string{"dash"},
	})
	if err != nil {
		t.Fatal(err)
	}
	post := accessRequest(http.MethodPost, accessAdministrationPath, string(body))
	post.Header.Set("Origin", "https://dash.example.test")
	post.Header.Set("Content-Type", "application/json")
	post.Header.Set("X-CSRF-Token", csrf)
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, post)
	if response.Code != http.StatusOK || len(authority.mutations) != 1 {
		t.Fatal("same-origin mutation did not reach the narrow authority exactly once", response.Code)
	}
	if decodeAccessResponse(t, response)["request_id"] != "00000000-0000-4000-8000-000000000001" {
		t.Fatal("mutation receipt omitted request identity")
	}
	if authority.mutations[0].ApplicationRoles != nil {
		t.Fatal("legacy browser update gained a role map")
	}
	body, err = json.Marshal(map[string]any{
		"action": "human-update", "request_id": "00000000-0000-4000-8000-000000000005", "expected_generation": "1", "csrf": csrf,
		"principal": authority.admitted.Principal, "disabled": false, "resources": []string{"dash"}, "application_roles": map[string]string{"dash": "member"},
	})
	if err != nil {
		t.Fatal(err)
	}
	post = accessRequest(http.MethodPost, accessAdministrationPath, string(body))
	post.Header.Set("Origin", "https://dash.example.test")
	post.Header.Set("Content-Type", "application/json")
	post.Header.Set("X-CSRF-Token", csrf)
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, post)
	if response.Code != http.StatusOK || len(authority.mutations) != 2 || authority.mutations[1].ApplicationRoles["dash"] != "member" {
		t.Fatal("role-bearing browser update was not forwarded exactly")
	}
}

func TestAccessAdministrationRejectsRequestAmbiguityBeforeAuthority(t *testing.T) {
	authority := &accessAuthorityStub{admitted: administrationAdmission(), inventory: administrationUsers()}
	browser := administrationBrowser(t, authority)
	for name, request := range map[string]*http.Request{
		"unknown-query":     accessRequest(http.MethodGet, accessAdministrationPath+"?kind=users&limit=50&extra=1", ""),
		"over-limit":        accessRequest(http.MethodGet, accessAdministrationPath+"?kind=users&limit=51", ""),
		"missing-cookie":    browserRequest(http.MethodGet, accessAdministrationPath+"?kind=users&limit=50", nil),
		"cross-origin-post": accessRequest(http.MethodPost, accessAdministrationPath, `{"action":"session-revoke"}`),
		"unexpected-method": accessRequest(http.MethodDelete, accessAdministrationPath, ""),
	} {
		t.Run(name, func(t *testing.T) {
			response := httptest.NewRecorder()
			browser.ServeHTTP(response, request)
			if response.Code != http.StatusBadRequest && response.Code != http.StatusUnauthorized && response.Code != http.StatusForbidden && response.Code != http.StatusNotFound {
				t.Fatal("ambiguous Access request reached authority", response.Code)
			}
			decodeAccessResponse(t, response)
		})
	}
	if authority.listCalls != 0 || len(authority.mutations) != 0 {
		t.Fatal("invalid Access request called the authority")
	}
}

func TestAccessAdministrationCSRFAndAuthorityErrorsAreFixed(t *testing.T) {
	authority := &accessAuthorityStub{admitted: administrationAdmission(), inventory: administrationUsers()}
	browser := administrationBrowser(t, authority)
	read := accessRequest(http.MethodGet, accessAdministrationPath+"?kind=users&limit=50", "")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, read)
	csrf := decodeAccessResponse(t, response)["csrf"].(string)
	body := `{"action":"session-revoke","request_id":"00000000-0000-4000-8000-000000000002","expected_generation":"1","csrf":"` + csrf + `","session_type":"terminal","session_id":"` + strings.Repeat("2", 32) + `"}`
	for name, configure := range map[string]func(*http.Request){
		"missing-origin": func(r *http.Request) {
			r.Header.Set("Content-Type", "application/json")
			r.Header.Set("X-CSRF-Token", csrf)
		},
		"csrf-mismatch": func(r *http.Request) {
			r.Header.Set("Origin", "https://dash.example.test")
			r.Header.Set("Content-Type", "application/json")
			r.Header.Set("X-CSRF-Token", "other")
		},
	} {
		t.Run(name, func(t *testing.T) {
			r := accessRequest(http.MethodPost, accessAdministrationPath, body)
			configure(r)
			response := httptest.NewRecorder()
			browser.ServeHTTP(response, r)
			if response.Code != http.StatusForbidden || len(authority.mutations) != 0 {
				t.Fatal("invalid CSRF/origin mutation reached authority", response.Code)
			}
			decodeAccessResponse(t, response)
		})
	}
	authority.mutateErr = administration.ErrConflict
	r := accessRequest(http.MethodPost, accessAdministrationPath, body)
	r.Header.Set("Origin", "https://dash.example.test")
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("X-CSRF-Token", csrf)
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, r)
	if response.Code != http.StatusConflict {
		t.Fatal("authority conflict was not safely classified", response.Code)
	}
	decodeAccessResponse(t, response)

	authority.inventory = administration.Inventory{Kind: "users", Items: []any{map[string]any{"id": authority.admitted.Principal}}}
	read = accessRequest(http.MethodGet, accessAdministrationPath+"?kind=users&limit=50", "")
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, read)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatal("malformed authority inventory was emitted", response.Code)
	}
	decodeAccessResponse(t, response)
}

func TestAccessAdministrationRejectsDuplicateAndOversizedMutationBodies(t *testing.T) {
	authority := &accessAuthorityStub{admitted: administrationAdmission(), inventory: administrationUsers()}
	browser := administrationBrowser(t, authority)
	read := accessRequest(http.MethodGet, accessAdministrationPath+"?kind=users&limit=50", "")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, read)
	csrf := decodeAccessResponse(t, response)["csrf"].(string)
	for name, body := range map[string]string{
		"duplicate-field": `{"action":"session-revoke","action":"session-revoke","request_id":"00000000-0000-4000-8000-000000000003","expected_generation":"1","csrf":"` + csrf + `","session_type":"terminal","session_id":"` + strings.Repeat("2", 32) + `"}`,
		"oversized":       `{"action":"` + strings.Repeat("a", maxAccessBody) + `"}`,
		"wildcard-grant":  `{"action":"human-update","request_id":"00000000-0000-4000-8000-000000000003","expected_generation":"1","csrf":"` + csrf + `","principal":"` + authority.admitted.Principal + `","disabled":false,"resources":["*"]}`,
		"empty-grants":    `{"action":"human-update","request_id":"00000000-0000-4000-8000-000000000003","expected_generation":"1","csrf":"` + csrf + `","principal":"` + authority.admitted.Principal + `","disabled":true,"resources":[]}`,
		"invalid-role":    `{"action":"human-update","request_id":"00000000-0000-4000-8000-000000000003","expected_generation":"1","csrf":"` + csrf + `","principal":"` + authority.admitted.Principal + `","disabled":false,"resources":["dash"],"application_roles":{"dash":"operator"}}`,
	} {
		t.Run(name, func(t *testing.T) {
			r := accessRequest(http.MethodPost, accessAdministrationPath, body)
			r.Header.Set("Origin", "https://dash.example.test")
			r.Header.Set("Content-Type", "application/json")
			r.Header.Set("X-CSRF-Token", csrf)
			response := httptest.NewRecorder()
			browser.ServeHTTP(response, r)
			if response.Code != http.StatusBadRequest || len(authority.mutations) != 0 {
				t.Fatal("malformed mutation reached authority", response.Code)
			}
			decodeAccessResponse(t, response)
		})
	}
	valid := `{"action":"session-revoke","request_id":"00000000-0000-4000-8000-000000000004","expected_generation":"1","csrf":"` + csrf + `","session_type":"terminal","session_id":"` + strings.Repeat("2", 32) + `"}`
	r := accessRequest(http.MethodPost, accessAdministrationPath, valid)
	r.Header.Set("Origin", "https://dash.example.test")
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("X-CSRF-Token", csrf)
	r.Header.Add("X-CSRF-Token", csrf)
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, r)
	if response.Code != http.StatusForbidden || len(authority.mutations) != 0 {
		t.Fatal("duplicate CSRF header reached authority", response.Code)
	}
	decodeAccessResponse(t, response)
}

func TestAccessAdministrationReservesOnlyItsExactPath(t *testing.T) {
	authority := &accessAuthorityStub{admitted: administrationAdmission(), inventory: administrationUsers()}
	browser := administrationBrowser(t, authority)
	r := accessRequest(http.MethodGet, accessAdministrationPath+"/other?kind=users&limit=50", "")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, r)
	if response.Code != http.StatusNotFound || authority.listCalls != 0 {
		t.Fatal("reserved Access subtree reached origin or authority", response.Code)
	}
	decodeAccessResponse(t, response)
}

func TestAccessAdministrationConstructorRequiresConfiguredExactOperatorRoute(t *testing.T) {
	declaration := administrationDeclaration()
	stub := &accessAuthorityStub{admitted: administrationAdmission(), inventory: administrationUsers()}
	if _, err := NewAccessAdministration(declaration, stub); err != nil {
		t.Fatal(err)
	}
	declaration.BrowserAdministration.Operators = nil
	if _, err := NewAccessAdministration(declaration, stub); err == nil {
		t.Fatal("unconfigured Access operators were accepted")
	}
	declaration = administrationDeclaration()
	declaration.BrowserAdministration.BrowserResource = "missing"
	if _, err := NewAccessAdministration(declaration, stub); err == nil {
		t.Fatal("unknown Access browser resource was accepted")
	}
}

func TestAccessAdministrationMapsDeniedAuthorityWithoutDetails(t *testing.T) {
	authority := &accessAuthorityStub{admitted: administrationAdmission(), inventory: administrationUsers(), listErr: administration.ErrDenied}
	browser := administrationBrowser(t, authority)
	r := accessRequest(http.MethodGet, accessAdministrationPath+"?kind=users&limit=50", "")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, r)
	if response.Code != http.StatusForbidden || strings.Contains(response.Body.String(), "administration denied") || !errors.Is(authority.listErr, administration.ErrDenied) {
		t.Fatal("authority denial leaked or was not classified", response.Code)
	}
	decodeAccessResponse(t, response)
}

func TestAccessAdministrationNormalizesRealIssuedSessionInventory(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	state, err := store.Open(root, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = state.Close() })
	var issuer *httptest.Server
	issuer = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"issuer": issuer.URL, "authorization_endpoint": issuer.URL + "/authorize", "token_endpoint": issuer.URL + "/token", "jwks_uri": issuer.URL + "/keys", "id_token_signing_alg_values_supported": []string{"RS256"},
		})
	}))
	t.Cleanup(issuer.Close)

	declaration := browserDeclaration("none")
	manager, err := session.New(context.Background(), state, []config.Rule{declaration.Resources[0].Rule}, session.Config{
		Issuer: issuer.URL, ClientID: "test-client", ClientSecret: "synthetic-test-secret", CallbackPath: "/_connect/callback",
		TransactionLifetime: time.Minute, SessionLifetime: time.Hour, MaxTransactions: 8, MaxPerBrowser: 2, HTTPClient: issuer.Client(),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(manager.Close)
	human, err := manager.SetHuman(issuer.URL, "operator", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	declaration.BrowserAdministration = &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{human.ID}}
	authority, err := administration.New(state, manager, declaration)
	if err != nil {
		t.Fatal(err)
	}

	id := strings.Repeat("3", 32)
	var admitted session.Admission
	if err := state.Update(func(tx *store.Tx) error {
		current := session.Session{ID: id, Principal: human.ID, PrincipalGeneration: human.Generation, Resource: "dash", Host: "dash.example.test", Generation: 1, Epoch: tx.Epoch(), IssuedAt: now.Add(-time.Minute), ExpiresAt: now.Add(time.Hour)}
		admitted = session.Admission{SessionID: id, SessionGeneration: current.Generation, Principal: human.ID, PrincipalGeneration: human.Generation, Resource: "dash", Host: "dash.example.test", Epoch: current.Epoch, ExpiresAt: current.ExpiresAt}
		return tx.Put("sessions", "session:"+id, current)
	}); err != nil {
		t.Fatal(err)
	}
	browserAuthority := newBrowserAuthorityStub()
	browserAuthority.admitted = admitted
	browser, err := NewBrowser(declaration, browserAuthority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {
		t.Error("Access endpoint reached origin dispatch")
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	handler, err := NewAccessAdministration(declaration, authority)
	if err != nil || browser.SetAccessAdministration(handler) != nil {
		t.Fatal("could not attach real administration authority", err)
	}

	response := httptest.NewRecorder()
	browser.ServeHTTP(response, accessRequest(http.MethodGet, accessAdministrationPath+"?kind=sessions&limit=50", ""))
	if response.Code != http.StatusOK {
		t.Fatal("real issued session inventory was rejected", response.Code, response.Body.String())
	}
	result := decodeAccessResponse(t, response)
	items, ok := result["items"].([]any)
	if !ok || len(items) != 1 {
		t.Fatal("real authority inventory was not emitted", result)
	}
	item, ok := items[0].(map[string]any)
	if !ok || item["id"] != id || item["status"] != "issued" {
		t.Fatal("real issued session status did not survive HTTP normalization", item)
	}
}
