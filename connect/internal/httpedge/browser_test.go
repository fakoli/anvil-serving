package httpedge

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/browseridentity"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/device"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

type browserAuthorityStub struct {
	mu       sync.Mutex
	begin    int
	complete int
	logout   int
	binding  string
	revoked  atomic.Bool
	admitted session.Admission
}

type loginCaptureAuthority struct {
	*browserAuthorityStub
	returnPaths      []string
	completionReturn string
}

func (s *loginCaptureAuthority) Begin(resource, returnPath, binding string) (session.Challenge, error) {
	if resource != "dash" || binding == "" {
		return session.Challenge{}, session.ErrDenied
	}
	s.mu.Lock()
	s.begin++
	s.binding = binding
	s.returnPaths = append(s.returnPaths, returnPath)
	s.mu.Unlock()
	return session.Challenge{AuthorizationURL: "https://idp.example.test/authorize?state=opaque", Binding: binding}, nil
}

func (s *loginCaptureAuthority) Complete(ctx context.Context, callback session.Callback) (session.Completion, error) {
	completion, err := s.browserAuthorityStub.Complete(ctx, callback)
	if err == nil && s.completionReturn != "" {
		completion.ReturnPath = s.completionReturn
	}
	return completion, err
}

func newBrowserAuthorityStub() *browserAuthorityStub {
	return &browserAuthorityStub{admitted: session.Admission{SessionID: "session-id", SessionGeneration: 1, Principal: "human-id", PrincipalGeneration: 1, Resource: "dash", Host: "dash.example.test", Epoch: "epoch", ExpiresAt: time.Date(2026, 9, 9, 13, 0, 0, 0, time.UTC)}}
}

func (s *browserAuthorityStub) Begin(resource, returnPath, binding string) (session.Challenge, error) {
	if resource != "dash" || returnPath != "/report" || binding == "" {
		return session.Challenge{}, session.ErrDenied
	}
	s.mu.Lock()
	s.begin++
	s.binding = binding
	s.mu.Unlock()
	return session.Challenge{AuthorizationURL: "https://idp.example.test/authorize?state=opaque", Binding: binding}, nil
}

func (s *browserAuthorityStub) Complete(_ context.Context, callback session.Callback) (session.Completion, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if callback.Host != "dash.example.test" || callback.State != "state" || callback.Code != "code" || callback.Binding == "" || callback.Binding != s.binding {
		return session.Completion{}, session.ErrDenied
	}
	s.complete++
	return session.Completion{Cookie: "opaque-session", Host: "dash.example.test", Resource: "dash", ReturnPath: "/report", ExpiresAt: s.admitted.ExpiresAt}, nil
}

func (s *browserAuthorityStub) Authenticate(raw, host string) (session.Admission, error) {
	if raw != "opaque-session" || host != s.admitted.Host || s.revoked.Load() {
		return session.Admission{}, session.ErrDenied
	}
	return s.admitted, nil
}

func (s *browserAuthorityStub) PortalSession(raw, host string) (session.Admission, session.Human, error) {
	admitted, err := s.Authenticate(raw, host)
	return admitted, session.Human{ID: admitted.Principal, Generation: admitted.PrincipalGeneration, Resources: []string{admitted.Resource}}, err
}

func (s *browserAuthorityStub) Check(admitted session.Admission) error {
	if s.revoked.Load() || admitted != s.admitted {
		return session.ErrDenied
	}
	return nil
}

func (s *browserAuthorityStub) CheckPrincipal(id string, generation uint64) error {
	if s.revoked.Load() || id != s.admitted.Principal || generation != s.admitted.PrincipalGeneration {
		return session.ErrDenied
	}
	return nil
}
func (s *browserAuthorityStub) CheckDeviceSession(id string, generation uint64, principal string, principalGeneration uint64) error {
	if s.revoked.Load() || id != s.admitted.SessionID || generation != s.admitted.SessionGeneration || principal != s.admitted.Principal || principalGeneration != s.admitted.PrincipalGeneration {
		return session.ErrDenied
	}
	return nil
}

func (s *browserAuthorityStub) Logout(raw, host string) error {
	if raw != "opaque-session" || host != s.admitted.Host {
		return session.ErrDenied
	}
	s.mu.Lock()
	s.logout++
	s.mu.Unlock()
	s.revoked.Store(true)
	return nil
}

func browserDeclaration(nativeAuth string) config.Gateway {
	limits := config.Limits{RequestBytes: 1024, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 3}
	return config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:17890", MaxConcurrent: 1, Resources: []config.Resource{{Rule: config.Rule{ID: "dash", Host: "dash.example.test", PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: nativeAuth, Limits: limits}, Connector: "origin-a", TunnelAddress: "127.0.0.1:17891"}}}
}

func browserFixture(t *testing.T, nativeAuth string, dispatch BrowserDispatch) (*Browser, *browserAuthorityStub) {
	t.Helper()
	authority := newBrowserAuthorityStub()
	browser, err := NewBrowser(browserDeclaration(nativeAuth), authority, dispatch)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	return browser, authority
}

func TestLoginUsesPortalHomeForDefaultAndRootReturn(t *testing.T) {
	declaration := browserDeclaration("none")
	declaration.Resources[0].Rule.PathPrefix = "/app"
	authority := &loginCaptureAuthority{browserAuthorityStub: newBrowserAuthorityStub(), completionReturn: "/app"}
	browser, err := NewBrowser(declaration, authority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	for _, target := range []string{BrowserLoginPath, BrowserLoginPath + "?return=%2Fapp", BrowserLoginPath + "?return=%2Fapp%2Fdeep"} {
		w := httptest.NewRecorder()
		browser.ServeHTTP(w, browserRequest(http.MethodGet, target, nil))
		if w.Code != http.StatusFound {
			t.Fatalf("login %s = %d", target, w.Code)
		}
	}
	want := []string{"/app/_anvil-connect/home", "/app/_anvil-connect/home", "/app/deep"}
	if strings.Join(authority.returnPaths, ",") != strings.Join(want, ",") {
		t.Fatalf("login return paths = %#v", authority.returnPaths)
	}
	callback := browserRequest(http.MethodGet, BrowserCallbackPath+"?state=state&code=code", nil)
	callback.AddCookie(&http.Cookie{Name: BrowserTransactionCookie, Value: authority.binding})
	w := httptest.NewRecorder()
	browser.ServeHTTP(w, callback)
	if w.Code != http.StatusSeeOther || w.Header().Get("Location") != "/app/_anvil-connect/home" {
		t.Fatalf("legacy root callback = %d %q", w.Code, w.Header().Get("Location"))
	}
}

func signedBrowserFixture(t *testing.T, dispatch BrowserDispatch) (*Browser, *browserAuthorityStub) {
	t.Helper()
	declaration := browserDeclaration("signed-identity")
	declaration.Resources[0].IdentityKeyEnv = "ANVIL_CONNECT_DASH_IDENTITY_KEY"
	declaration.Resources[0].IdentityKeyID = "dash-v1"
	authority := newBrowserAuthorityStub()
	authority.admitted = session.Admission{SessionID: strings.Repeat("1", 32), SessionGeneration: 1, Principal: "human:" + strings.Repeat("a", 64), PrincipalGeneration: 1, Resource: "dash", Host: "dash.example.test", Epoch: strings.Repeat("b", 64), ExpiresAt: time.Now().Add(time.Minute).UTC()}
	signer, err := browseridentity.NewSigner("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8", "dash-v1")
	if err != nil {
		t.Fatal(err)
	}
	browser, err := NewBrowserWithIdentity(declaration, authority, map[string]*browseridentity.Signer{"dash": signer}, dispatch)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	return browser, authority
}

func browserRequest(method, target string, body io.Reader) *http.Request {
	r := httptest.NewRequest(method, target, body)
	r.Host = "dash.example.test"
	return r
}

func cookieValue(t *testing.T, recorder *httptest.ResponseRecorder, name string) *http.Cookie {
	t.Helper()
	for _, cookie := range recorder.Result().Cookies() {
		if cookie.Name == name {
			return cookie
		}
	}
	t.Fatalf("cookie %q missing", name)
	return nil
}

func TestBrowserLoginCallbackAndNativeHeaders(t *testing.T) {
	var dispatched atomic.Int32
	browser, authority := browserFixture(t, "passthrough", func(w http.ResponseWriter, r *http.Request, resource config.Resource, admitted session.Admission) {
		dispatched.Add(1)
		if resource.Rule.ID != "dash" || admitted.SessionID != "session-id" || admitted.Principal != "human-id" {
			t.Error("browser admission was not preserved")
		}
		if r.Header.Get("Cookie") != "native=value" || r.Header.Get("X-CSRF-Token") != "csrf" || r.Header.Get("Authorization") != "Bearer native" {
			t.Error("native browser state was not preserved")
		}
		for _, name := range []string{"Proxy-Authorization", "X-Anvil-Connect-Authorization", "X-Anvil-Connect-Resource", "X-Forwarded-Host", "Remote-User", "Remote-Groups", "Remote-Name", "X-Auth-Request-Email", "X-Authenticated-User", "Cf-Access-Identity", "X-Goog-Authenticated-User-Email", "X-Amzn-Oidc-Identity", "Tailscale-User-Login"} {
			if r.Header.Get(name) != "" {
				t.Error("spoofed identity reached native application")
			}
		}
		w.Header().Set("X-Native-Security", "preserved")
		w.WriteHeader(http.StatusNoContent)
	})

	ordinary := browserRequest(http.MethodGet, "/report", nil)
	ordinary.Header.Set("Accept", "text/html")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, ordinary)
	if response.Code != http.StatusFound || response.Header().Get("Location") != BrowserLoginPath+"?return=%2Freport" || dispatched.Load() != 0 {
		t.Fatal("unauthenticated document did not enter confined login")
	}

	login := browserRequest(http.MethodGet, BrowserLoginPath+"?return=%2Freport", nil)
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, login)
	if response.Code != http.StatusFound || response.Header().Get("Location") != "https://idp.example.test/authorize?state=opaque" || authority.begin != 1 {
		t.Fatal("login was not started")
	}
	transaction := cookieValue(t, response, BrowserTransactionCookie)
	if !transaction.Secure || !transaction.HttpOnly || transaction.Path != "/" || transaction.Domain != "" || transaction.SameSite != http.SameSiteLaxMode || transaction.Value != authority.binding {
		t.Fatal("transaction binding cookie was not host-only and strict")
	}

	callback := browserRequest(http.MethodGet, BrowserCallbackPath+"?state=state&code=code", nil)
	callback.Header.Set("Cookie", BrowserTransactionCookie+"="+transaction.Value)
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, callback)
	if response.Code != http.StatusSeeOther || response.Header().Get("Location") != "/report" || authority.complete != 1 {
		t.Fatal("callback did not establish a replacement session")
	}
	sessionCookie := cookieValue(t, response, BrowserSessionCookie)
	if !sessionCookie.Secure || !sessionCookie.HttpOnly || sessionCookie.Path != "/" || sessionCookie.Domain != "" || sessionCookie.SameSite != http.SameSiteLaxMode || sessionCookie.Value != "opaque-session" {
		t.Fatal("session cookie was not host-only and strict")
	}

	request := browserRequest(http.MethodPost, "/report", strings.NewReader("{}"))
	request.Header.Set("Origin", "https://dash.example.test")
	request.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session; "+BrowserTransactionCookie+"=transaction")
	request.Header.Add("Cookie", "native=value")
	request.Header.Set("Authorization", "Bearer native")
	request.Header.Set("X-CSRF-Token", "csrf")
	request.Header.Set("Proxy-Authorization", "Basic proxy")
	request.Header.Set("X-Anvil-Connect-Authorization", "forged")
	request.Header.Set("X-Forwarded-Host", "admin.example.test")
	request.Header.Set("Remote-User", "forged")
	request.Header.Set("Remote-Groups", "forged")
	request.Header.Set("Remote-Name", "forged")
	request.Header.Set("X-Auth-Request-Email", "forged")
	request.Header.Set("X-Authenticated-User", "forged")
	request.Header.Set("Cf-Access-Identity", "forged")
	request.Header.Set("X-Goog-Authenticated-User-Email", "forged")
	request.Header.Set("X-Amzn-Oidc-Identity", "forged")
	request.Header.Set("Tailscale-User-Login", "forged")
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, request)
	if response.Code != http.StatusNoContent || dispatched.Load() != 1 {
		t.Fatal("authenticated same-origin request did not dispatch")
	}
}

func TestBrowserRejectsOriginCookieAndCallbackAmbiguity(t *testing.T) {
	var dispatched atomic.Int32
	browser, authority := browserFixture(t, "none", func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) { dispatched.Add(1) })
	for name, origins := range map[string][]string{
		"missing":    nil,
		"cross-site": {"https://evil.example.test"},
		"null":       {"null"},
		"duplicate":  {"https://dash.example.test", "https://dash.example.test"},
	} {
		t.Run(name, func(t *testing.T) {
			r := browserRequest(http.MethodPost, "/report", strings.NewReader("{}"))
			r.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
			if origins != nil {
				r.Header["Origin"] = origins
			}
			response := httptest.NewRecorder()
			browser.ServeHTTP(response, r)
			if response.Code != http.StatusForbidden || dispatched.Load() != 0 {
				t.Fatal("unsafe origin reached browser dispatcher")
			}
		})
	}
	for name, mutate := range map[string]func(*http.Request){
		"duplicate-session": func(r *http.Request) {
			r.Header.Set("Cookie", BrowserSessionCookie+"=one; "+BrowserSessionCookie+"=two")
		},
		"multiple-cookie-lines": func(r *http.Request) {
			r.Header["Cookie"] = []string{BrowserSessionCookie + "=one", BrowserSessionCookie + "=two"}
		},
		"sibling-reserved": func(r *http.Request) { r.Header.Set("Cookie", BrowserSessionCookie+"-evil=value") },
		"malformed":        func(r *http.Request) { r.Header.Set("Cookie", "unterminated") },
	} {
		t.Run(name, func(t *testing.T) {
			r := browserRequest(http.MethodGet, "/report", nil)
			mutate(r)
			response := httptest.NewRecorder()
			browser.ServeHTTP(response, r)
			if response.Code != http.StatusBadRequest || dispatched.Load() != 0 {
				t.Fatal("ambiguous cookie reached browser dispatcher")
			}
		})
	}

	for _, target := range []string{BrowserLoginPath + "?return=//evil.example.test", BrowserLoginPath + "?return=https://evil.example.test", BrowserLoginPath + "?return=%2F%252fadmin"} {
		response := httptest.NewRecorder()
		browser.ServeHTTP(response, browserRequest(http.MethodGet, target, nil))
		if response.Code != http.StatusBadRequest {
			t.Fatal("login return path escaped resource")
		}
	}
	for _, target := range []string{BrowserCallbackPath + "?state=one&state=two&code=code", BrowserCallbackPath + "?state=state&code=code&error=access_denied", BrowserCallbackPath + "?state=state&code=code"} {
		response := httptest.NewRecorder()
		browser.ServeHTTP(response, browserRequest(http.MethodGet, target, nil))
		if response.Code != http.StatusBadRequest || authority.complete != 0 {
			t.Fatal("ambiguous callback was consumed")
		}
	}
	for _, control := range []struct {
		method string
		target string
		origin string
	}{
		{http.MethodGet, BrowserLoginPath, ""},
		{http.MethodGet, BrowserCallbackPath + "?state=state&code=code", ""},
		{http.MethodPost, BrowserLogoutPath, "https://dash.example.test"},
	} {
		r := browserRequest(control.method, control.target, strings.NewReader("payload"))
		if control.origin != "" {
			r.Header.Set("Origin", control.origin)
		}
		r.Header.Set("Cookie", BrowserTransactionCookie+"=binding; "+BrowserSessionCookie+"=opaque-session")
		response := httptest.NewRecorder()
		browser.ServeHTTP(response, r)
		if response.Code != http.StatusForbidden || authority.begin != 0 || authority.complete != 0 || authority.logout != 0 {
			t.Fatal("control path accepted a request payload")
		}
	}
	unknownBody := browserRequest(http.MethodGet, BrowserLoginPath, strings.NewReader("payload"))
	unknownBody.ContentLength = -1
	unknownBody.ProtoMajor = 2
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, unknownBody)
	if response.Code != http.StatusForbidden || authority.begin != 0 {
		t.Fatal("control path accepted an unknown-length body")
	}
	controlUpgrade := browserRequest(http.MethodGet, BrowserLoginPath, nil)
	controlUpgrade.Header.Set("Connection", "upgrade")
	controlUpgrade.Header.Set("Upgrade", "websocket")
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, controlUpgrade)
	if response.Code != http.StatusForbidden || authority.begin != 0 {
		t.Fatal("control upgrade reached login authority")
	}

	upgrade := browserRequest(http.MethodGet, "/report", nil)
	upgrade.Header.Set("Connection", "upgrade")
	upgrade.Header.Set("Upgrade", "websocket")
	upgrade.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, upgrade)
	if response.Code != http.StatusForbidden || dispatched.Load() != 0 {
		t.Fatal("unauthenticated websocket handshake reached dispatcher")
	}
}

func TestBrowserLogoutAndHeaderModes(t *testing.T) {
	browser, authority := browserFixture(t, "none", func(w http.ResponseWriter, r *http.Request, _ config.Resource, _ session.Admission) {
		if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "native=value" || r.Header.Get("X-CSRF-Token") != "csrf" {
			t.Error("none mode did not clean identity while preserving native browser state")
		}
		w.WriteHeader(http.StatusNoContent)
	})
	request := browserRequest(http.MethodPost, "/report", strings.NewReader("{}"))
	request.Header.Set("Origin", "https://dash.example.test")
	request.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session; native=value")
	request.Header.Set("Authorization", "Bearer must-not-forward")
	request.Header.Set("X-CSRF-Token", "csrf")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, request)
	if response.Code != http.StatusNoContent {
		t.Fatal("browser none mode rejected valid request")
	}
	for _, origin := range []string{"", "https://evil.example.test", "null"} {
		r := browserRequest(http.MethodPost, BrowserLogoutPath, nil)
		r.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
		if origin != "" {
			r.Header.Set("Origin", origin)
		}
		response := httptest.NewRecorder()
		browser.ServeHTTP(response, r)
		if response.Code != http.StatusForbidden || authority.logout != 0 {
			t.Fatal("logout accepted an inexact origin")
		}
	}
	logout := browserRequest(http.MethodPost, BrowserLogoutPath, nil)
	logout.Header.Set("Origin", "https://dash.example.test")
	logout.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, logout)
	if response.Code != http.StatusSeeOther || authority.logout != 1 || response.Header().Get("Location") != "/" {
		t.Fatal("same-origin logout did not revoke the Connect session")
	}
	cleared := cookieValue(t, response, BrowserSessionCookie)
	if cleared.MaxAge >= 0 || cleared.Path != "/" || !cleared.Secure || !cleared.HttpOnly || cleared.SameSite != http.SameSiteLaxMode {
		t.Fatal("logout did not clear the host-only Connect cookie")
	}

	header := http.Header{"Authorization": {"Bearer one", "Bearer two"}}
	if CleanBrowserHeaders(header, "passthrough") == nil {
		t.Fatal("passthrough mode accepted ambiguous native authorization")
	}
	header = http.Header{"X-Api-Key": {"native-key"}}
	if CleanBrowserHeaders(header, "passthrough") != nil || header.Get("X-Api-Key") != "native-key" {
		t.Fatal("passthrough mode did not preserve its explicit native API key")
	}
	header = http.Header{"X-Api-Key": {"native-key"}}
	if CleanBrowserHeaders(header, "none") != nil || header.Get("X-Api-Key") != "" {
		t.Fatal("none mode forwarded a native API key")
	}
}

func TestBrowserResponseValidationPreservesNativeHeaders(t *testing.T) {
	rule := browserDeclaration("none").Resources[0].Rule
	valid := &http.Response{Header: http.Header{"Location": {"https://dash.example.test/report"}, "Set-Cookie": {"native=value; Path=/; Secure; HttpOnly"}, "Content-Security-Policy": {"default-src 'self'"}, "X-Frame-Options": {"SAMEORIGIN"}}}
	if err := ValidateBrowserResponse(valid, rule); err != nil || valid.Header.Get("Content-Security-Policy") == "" || valid.Header.Get("X-Frame-Options") == "" {
		t.Fatal("native browser response was rewritten or denied", err)
	}
	for name, header := range map[string]http.Header{
		"cross-host":           {"Location": {"https://evil.example.test/report"}},
		"network-path":         {"Location": {"//evil.example.test/report"}},
		"escaped-path":         {"Location": {"/../admin"}},
		"parent-domain-cookie": {"Set-Cookie": {"native=value; Domain=example.test; Path=/"}},
		"reserved-cookie":      {"Set-Cookie": {BrowserSessionCookie + "=forged; Path=/; Secure; HttpOnly"}},
		"two-locations":        {"Location": {"/report", "/other"}},
	} {
		t.Run(name, func(t *testing.T) {
			if ValidateBrowserResponse(&http.Response{Header: header}, rule) == nil {
				t.Fatal("unsafe native browser response accepted")
			}
		})
	}
}

func TestBrowserCapacityAndRevocationCancelDispatch(t *testing.T) {
	started, stopped := make(chan struct{}), make(chan struct{})
	browser, authority := browserFixture(t, "none", func(_ http.ResponseWriter, r *http.Request, _ config.Resource, _ session.Admission) {
		close(started)
		<-r.Context().Done()
		close(stopped)
	})
	server := httptest.NewServer(browser)
	defer server.Close()
	request, err := http.NewRequest(http.MethodPost, server.URL+"/report", strings.NewReader("{}"))
	if err != nil {
		t.Fatal(err)
	}
	request.Host = "dash.example.test"
	request.Header.Set("Origin", "https://dash.example.test")
	request.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	done := make(chan struct{})
	go func() {
		defer close(done)
		response, _ := server.Client().Do(request)
		if response != nil {
			response.Body.Close()
		}
	}()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("browser request was not admitted")
	}
	second := browserRequest(http.MethodPost, "/report", strings.NewReader("{}"))
	second.Header.Set("Origin", "https://dash.example.test")
	second.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, second)
	if response.Code != http.StatusTooManyRequests {
		t.Fatal("browser admission capacity was not bounded")
	}
	authority.revoked.Store(true)
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("browser stream was not canceled after session revocation")
	}
	<-done
}

func TestSignedIdentityUsesPrivateContextAndRevokes(t *testing.T) {
	var dispatched atomic.Int32
	var authority *browserAuthorityStub
	browser, authority := signedBrowserFixture(t, func(w http.ResponseWriter, r *http.Request, resource config.Resource, admitted session.Admission) {
		dispatched.Add(1)
		if resource.Rule.NativeAuth != "signed-identity" || admitted != authority.admitted || r.Header.Get(browseridentity.Header) != "" {
			t.Error("signed identity header or admission crossed an unsafe boundary")
		}
		assertion, ok := browseridentity.Assertion(r.Context())
		if !ok || !strings.HasPrefix(assertion, "acai1.") {
			t.Error("signed identity was not retained in private request context")
		}
		w.WriteHeader(http.StatusNoContent)
	})
	request := browserRequest(http.MethodGet, "/api/observatory/v1/session?view=current", nil)
	request.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	request.Header.Set(browseridentity.Header, "acai1.e30.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, request)
	if response.Code != http.StatusNoContent || dispatched.Load() != 1 {
		t.Fatal("signed browser request was not admitted")
	}
	authority.revoked.Store(true)
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized || dispatched.Load() != 1 {
		t.Fatal("revoked Connect session minted or dispatched an identity")
	}
}

func TestSignedIdentityRequiresSignerAndUsesExplicitLogoutLanding(t *testing.T) {
	declaration := browserDeclaration("signed-identity")
	declaration.Resources[0].IdentityKeyEnv = "ANVIL_CONNECT_DASH_IDENTITY_KEY"
	declaration.Resources[0].IdentityKeyID = "dash-v1"
	authority := newBrowserAuthorityStub()
	if browser, err := NewBrowser(declaration, authority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {}); err == nil || browser != nil {
		t.Fatal("signed identity resource started without its signer")
	}
	browser, authority := signedBrowserFixture(t, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {})
	logout := browserRequest(http.MethodPost, BrowserLogoutPath, nil)
	logout.Header.Set("Origin", "https://dash.example.test")
	logout.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	response := httptest.NewRecorder()
	browser.ServeHTTP(response, logout)
	if response.Code != http.StatusOK || response.Header().Get("Cache-Control") != "no-store" || response.Header().Get("Content-Type") != "text/html; charset=utf-8" || !strings.Contains(response.Body.String(), `href="/"`) || !strings.Contains(response.Body.String(), "Sign in again") {
		t.Fatal("signed identity logout did not render the explicit no-store landing")
	}
	if strings.Contains(response.Body.String(), "<script") || authority.logout != 1 {
		t.Fatal("signed identity logout landing was unsafe or did not revoke")
	}
	cleared := cookieValue(t, response, BrowserSessionCookie)
	if cleared.MaxAge >= 0 || !cleared.Secure || !cleared.HttpOnly {
		t.Fatal("signed identity logout did not clear Connect cookie")
	}
	request := browserRequest(http.MethodGet, "/", nil)
	request.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	response = httptest.NewRecorder()
	browser.ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatal("revoked signed identity session was still admitted")
	}
}

func TestSignedIdentityCallbackCompletesBeforeAnyAssertionIsMinted(t *testing.T) {
	browser, authority := signedBrowserFixture(t, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {
		t.Fatal("OIDC callback must not dispatch to the native application")
	})
	login := browserRequest(http.MethodGet, BrowserLoginPath+"?return=%2Freport", nil)
	loginResponse := httptest.NewRecorder()
	browser.ServeHTTP(loginResponse, login)
	if loginResponse.Code != http.StatusFound || authority.begin != 1 {
		t.Fatal("signed identity login did not start the ordinary Connect transaction")
	}
	callback := browserRequest(http.MethodGet, BrowserCallbackPath+"?state=state&code=code", nil)
	callback.Header.Set("Cookie", BrowserTransactionCookie+"="+authority.binding)
	callbackResponse := httptest.NewRecorder()
	browser.ServeHTTP(callbackResponse, callback)
	if callbackResponse.Code != http.StatusSeeOther || callbackResponse.Header().Get("Location") != "/report" || authority.complete != 1 {
		t.Fatal("signed identity callback did not complete the ordinary Connect session")
	}
	if callbackResponse.Header().Get(browseridentity.Header) != "" || cookieValue(t, callbackResponse, BrowserSessionCookie).Value != "opaque-session" {
		t.Fatal("callback exposed an identity assertion instead of only a Connect session cookie")
	}
}

func TestDeviceApprovalRouteIsReservedAndIssuesOneMemoryOnlyCredential(t *testing.T) {
	limits := config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 30}
	human := "human:" + strings.Repeat("a", 64)
	gateway := config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:17890", MaxConcurrent: 4, Resources: []config.Resource{
		{Rule: config.Rule{ID: "dash", Host: "dash.example.test", PathPrefix: "/app", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none", Limits: limits}, Connector: "dash-origin", TunnelAddress: "127.0.0.1:17891"},
		{Rule: config.Rule{ID: "router", Host: "api.example.test", PathPrefix: "/v1", Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer", Limits: limits}, Connector: "api-origin", TunnelAddress: "127.0.0.1:17892"},
	}, DeviceAuthorizations: []config.DeviceAuthorization{{BrowserResource: "dash", APIResource: "router", Methods: []string{"POST"}, Label: "Synthetic <device>", Principals: map[string]string{human: "owner"}}}}
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer state.Close()
	keys, err := access.NewKeys(state, []config.Rule{gateway.Resources[1].Rule})
	if err != nil || keys.SetPrincipal("owner", []access.Grant{{Resource: "router", Methods: []string{"POST"}}}, false) != nil {
		t.Fatal("api principal setup failed")
	}
	authority := newBrowserAuthorityStub()
	authority.admitted = session.Admission{SessionID: strings.Repeat("1", 32), SessionGeneration: 1, Principal: human, PrincipalGeneration: 1, Resource: "dash", Host: "dash.example.test", Epoch: strings.Repeat("b", 64), ExpiresAt: time.Now().Add(time.Hour).UTC()}
	devices, err := device.New(state, gateway, authority, keys)
	if err != nil {
		t.Fatal(err)
	}
	var dispatched atomic.Int32
	browser, err := NewBrowserWithIdentityAndDevice(gateway, authority, nil, devices, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) { dispatched.Add(1) })
	if err != nil {
		t.Fatal(err)
	}
	defer browser.Close()
	path := "/app/_anvil-connect/device"
	start := browserRequest(http.MethodPost, path+"/start", nil)
	startResponse := httptest.NewRecorder()
	browser.ServeHTTP(startResponse, start)
	var started struct {
		DeviceCode string `json:"device_code"`
		UserCode   string `json:"user_code"`
	}
	if startResponse.Code != http.StatusOK || json.Unmarshal(startResponse.Body.Bytes(), &started) != nil || len(started.UserCode) != 8 || dispatched.Load() != 0 {
		t.Fatal("reserved device start reached dashboard or failed")
	}
	login := browserRequest(http.MethodGet, path, nil)
	login.Header.Set("Accept", "text/html")
	loginResponse := httptest.NewRecorder()
	browser.ServeHTTP(loginResponse, login)
	if loginResponse.Code != http.StatusFound || dispatched.Load() != 0 {
		t.Fatal("unauthenticated approval did not enter Connect login")
	}
	binding, ok := devices.Binding("dash")
	if !ok {
		t.Fatal("device binding unavailable")
	}
	form := url.Values{"user_code": {started.UserCode}, "decision": {"approve"}, "csrf": {devices.CSRF(authority.admitted, binding)}}
	approve := browserRequest(http.MethodPost, path, strings.NewReader(form.Encode()))
	approve.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	approve.Header.Set("Origin", "https://dash.example.test")
	approve.Header.Set("Cookie", BrowserSessionCookie+"=opaque-session")
	approved := httptest.NewRecorder()
	browser.ServeHTTP(approved, approve)
	if approved.Code != http.StatusOK || strings.Contains(approved.Body.String(), "<device>") || dispatched.Load() != 0 {
		t.Fatal("approval escaped or reached dashboard origin")
	}
	pollPayload, _ := json.Marshal(map[string]string{"device_code": started.DeviceCode})
	poll := browserRequest(http.MethodPost, path+"/poll", strings.NewReader(string(pollPayload)))
	poll.Header.Set("Content-Type", "application/json")
	pollResponse := httptest.NewRecorder()
	browser.ServeHTTP(pollResponse, poll)
	var result struct {
		Status string `json:"status"`
		Token  string `json:"access_token"`
	}
	if pollResponse.Code != http.StatusOK || json.Unmarshal(pollResponse.Body.Bytes(), &result) != nil || result.Status != "approved" || result.Token == "" || dispatched.Load() != 0 {
		t.Fatal("approved device was not redeemed through reserved route")
	}
	if _, err := keys.Authenticate(result.Token, "router", "POST"); err != nil {
		t.Fatal("device credential did not obey mapped API grant")
	}
}

func TestDeviceRequestRejectsDuplicateAndOversizeJSON(t *testing.T) {
	for _, body := range []string{
		`{"device_code":"first","device_code":"second"}`,
		strings.Repeat("x", 513),
	} {
		r := httptest.NewRequest(http.MethodPost, "https://dash.example.test/app/_anvil-connect/device/poll", strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		if _, ok := readDeviceRequest(httptest.NewRecorder(), r); ok {
			t.Fatal("ambiguous or oversized device JSON accepted")
		}
	}
}
