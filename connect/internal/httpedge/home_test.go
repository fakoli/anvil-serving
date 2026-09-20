package httpedge

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

type homeStub struct{ *portalStub }

func (s *homeStub) BeginPortal(binding string) (session.Challenge, error) {
	s.binding = binding
	return session.Challenge{AuthorizationURL: "https://idp.example.test/authorize?state=opaque", Binding: binding}, nil
}

func (s *homeStub) Complete(_ context.Context, c session.Callback) (session.Completion, error) {
	if c.Host != "home.example.test" || c.Binding == "" || c.Binding != s.binding || c.State != "state" || c.Code != "code" {
		return session.Completion{}, session.ErrDenied
	}
	return session.Completion{Cookie: "opaque-session", Host: c.Host, Resource: "_connect-home", ReturnPath: "/_anvil-connect/home", ExpiresAt: s.admitted.ExpiresAt}, nil
}

func TestDedicatedHomeRoutesAndEntitlements(t *testing.T) {
	declaration := browserDeclaration("none")
	declaration.PortalHost = "home.example.test"
	workbench, grafana := "Workbench", "Grafana"
	declaration.Resources[0].DisplayName = &workbench
	other := declaration.Resources[0]
	other.Rule.ID, other.Rule.Host, other.TunnelAddress = "grafana", "graph.example.test", "127.0.0.1:17892"
	other.DisplayName = &grafana
	declaration.Resources = append(declaration.Resources, other)
	operator := "human:" + strings.Repeat("a", 64)
	declaration.BrowserAdministration = &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{operator}}
	authority := &homeStub{&portalStub{browserAuthorityStub: newBrowserAuthorityStub(), human: session.Human{ID: operator, Generation: 1, Resources: []string{"dash"}}}}
	authority.admitted.Host, authority.admitted.Resource, authority.admitted.Principal = declaration.PortalHost, "_connect-home", operator
	home, err := NewHome(declaration, authority)
	if err != nil {
		t.Fatal(err)
	}
	const base = "/_anvil-connect/home"
	request := func(method, path, host, origin, cookie string) *httptest.ResponseRecorder {
		r := browserRequest(method, path, nil)
		r.Host = host
		r.Header.Set("Accept", "text/html")
		if origin != "" {
			r.Header.Set("Origin", origin)
		}
		if cookie != "" {
			r.AddCookie(&http.Cookie{Name: BrowserSessionCookie, Value: cookie})
		}
		w := httptest.NewRecorder()
		home.ServeHTTP(w, r)
		return w
	}
	get := func(path, cookie string) *httptest.ResponseRecorder {
		return request("GET", path, declaration.PortalHost, "", cookie)
	}
	if w := get("/", ""); w.Code != 302 || w.Header().Get("Location") != base {
		t.Fatalf("root must redirect to fixed home subtree: %d %q", w.Code, w.Header().Get("Location"))
	}
	if w := get(base, ""); w.Code != 302 || !strings.HasPrefix(w.Header().Get("Location"), BrowserLoginPath) {
		t.Fatalf("anonymous home must sign in: %d", w.Code)
	}
	for _, suffix := range []string{"/home.css", "/home.js", "/logo.png", "/favicon.ico"} {
		if w := get(base+suffix, ""); w.Code != 200 || w.Body.Len() == 0 || w.Header().Get("Content-Type") == "" {
			t.Fatalf("rendered asset path %s unavailable: %d", suffix, w.Code)
		}
	}
	if w := get(base, "opaque-session"); w.Code != 200 || !strings.Contains(w.Body.String(), base+"/home.js") || !strings.Contains(w.Body.String(), `data-home="`+base+`"`) {
		t.Fatalf("page does not use rendered subtree: %d", w.Code)
	}
	for _, cookie := range []string{"", "foreign-app-session"} {
		if w := get(base+"/data", cookie); w.Code != 401 {
			t.Fatalf("data admitted missing/foreign cookie: %d", w.Code)
		}
	}
	var data struct {
		Services             []portalService `json:"services"`
		Administration       string          `json:"administration_url"`
		InlineAdministration string          `json:"administration_path"`
	}
	read := func() {
		t.Helper()
		w := get(base+"/data", "opaque-session")
		data.Services, data.Administration, data.InlineAdministration = nil, "", ""
		if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &data) != nil {
			t.Fatalf("service inventory: %d", w.Code)
		}
	}
	read()
	if len(data.Services) != 1 || data.Services[0].ID != "dash" || data.Services[0].Name != "Workbench" || data.Administration != "https://dash.example.test"+base || data.InlineAdministration != "" {
		t.Fatalf("unexpected grants or administration authority: %#v", data)
	}
	authority.human.Resources = []string{"dash", "grafana"}
	read()
	if len(data.Services) != 2 || data.Services[1].Name != "Grafana" || data.Services[1].URL != "https://graph.example.test/" {
		t.Fatalf("Grafana tile did not follow granted resource: %#v", data.Services)
	}
	authority.human.Resources = []string{"grafana"}
	read()
	if data.Administration != "" {
		t.Fatal("operator without administration-service grant received management link")
	}
	authority.human.Resources = []string{"dash"}
	authority.human.ID, authority.admitted.Principal = "human:"+strings.Repeat("b", 64), "human:"+strings.Repeat("b", 64)
	read()
	if data.Administration != "" {
		t.Fatal("non-operator received management link")
	}
	for _, path := range []string{"/data", "/home.js", "/arbitrary", "/_anvil-connect/access", base + "/unknown"} {
		if w := get(path, "opaque-session"); w.Code != 404 {
			t.Fatalf("unknown home route %s admitted: %d", path, w.Code)
		}
	}
	if w := request("GET", base+"/data", "dash.example.test", "", "opaque-session"); w.Code != 404 {
		t.Fatal("foreign host admitted")
	}
	for _, path := range []string{base, base + "/data", BrowserLoginPath} {
		if w := request("GET", path, declaration.PortalHost, "https://foreign.example.test", "opaque-session"); w.Code < 400 {
			t.Fatalf("foreign origin admitted at %s", path)
		}
	}
	if w := request("POST", BrowserLogoutPath, declaration.PortalHost, "https://foreign.example.test", "opaque-session"); w.Code != 403 {
		t.Fatal("cross-origin logout admitted")
	}
	if w := request("POST", BrowserLogoutPath+"?extra=1", declaration.PortalHost, "https://"+declaration.PortalHost, "opaque-session"); w.Code < 400 {
		t.Fatal("logout query admitted")
	}
	authority.human.Disabled = true
	if w := get(base+"/data", "opaque-session"); w.Code != 401 {
		t.Fatal("disabled account admitted")
	}
}

func TestDedicatedHomeKeepsApplicationRootDestination(t *testing.T) {
	declaration := browserDeclaration("none")
	declaration.PortalHost = "home.example.test"
	declaration.Resources[0].Rule.PathPrefix = "/app"
	authority := &loginCaptureAuthority{browserAuthorityStub: newBrowserAuthorityStub(), completionReturn: "/app"}
	browser, err := NewBrowser(declaration, authority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	for _, target := range []string{BrowserLoginPath, BrowserLoginPath + "?return=%2Fapp", BrowserLoginPath + "?return=%2Fapp%2F_anvil-connect%2Fhome"} {
		w := httptest.NewRecorder()
		browser.ServeHTTP(w, browserRequest(http.MethodGet, target, nil))
		if w.Code != http.StatusFound {
			t.Fatalf("login %s: %d", target, w.Code)
		}
	}
	if strings.Join(authority.returnPaths, ",") != "/app,/app,/app/_anvil-connect/home" {
		t.Fatalf("tile login sent back to chooser: %#v", authority.returnPaths)
	}
	r := browserRequest(http.MethodGet, BrowserCallbackPath+"?state=state&code=code", nil)
	r.AddCookie(&http.Cookie{Name: BrowserTransactionCookie, Value: authority.binding})
	w := httptest.NewRecorder()
	browser.ServeHTTP(w, r)
	if w.Code != http.StatusSeeOther || w.Header().Get("Location") != "/app" {
		t.Fatalf("application callback: %d %q", w.Code, w.Header().Get("Location"))
	}
}

func TestDedicatedHomeLoginCallbackAndLogout(t *testing.T) {
	declaration := browserDeclaration("none")
	declaration.PortalHost = "home.example.test"
	authority := &homeStub{&portalStub{browserAuthorityStub: newBrowserAuthorityStub()}}
	authority.admitted.Host, authority.admitted.Resource = declaration.PortalHost, "_connect-home"
	home, err := NewHome(declaration, authority)
	if err != nil {
		t.Fatal(err)
	}
	request := func(method, path string, cookies ...*http.Cookie) *httptest.ResponseRecorder {
		r := browserRequest(method, path, nil)
		r.Host = declaration.PortalHost
		if method == "POST" {
			r.Header.Set("Origin", "https://"+r.Host)
		}
		for _, cookie := range cookies {
			r.AddCookie(cookie)
		}
		w := httptest.NewRecorder()
		home.ServeHTTP(w, r)
		return w
	}
	login := request("GET", BrowserLoginPath)
	if login.Code != 302 || !strings.HasPrefix(login.Header().Get("Location"), "https://idp.example.test/") {
		t.Fatalf("login: %d", login.Code)
	}
	cookies := login.Result().Cookies()
	if len(cookies) != 1 || cookies[0].Name != BrowserTransactionCookie || cookies[0].Domain != "" || !cookies[0].HttpOnly || !cookies[0].Secure || cookies[0].Path != "/" {
		t.Fatal("transaction cookie must be host-bound and protected")
	}
	for _, query := range []string{"state=state&code=code&scope=email", "state=state&code=code&iss=", "state=state&state=again&code=code"} {
		if w := request("GET", BrowserCallbackPath+"?"+query, cookies...); w.Code != 400 {
			t.Fatalf("ambiguous callback admitted: %d", w.Code)
		}
	}
	callback := request("GET", BrowserCallbackPath+"?state=state&code=code", cookies...)
	if callback.Code != 303 || callback.Header().Get("Location") != "/_anvil-connect/home" {
		t.Fatalf("callback: %d %q", callback.Code, callback.Header().Get("Location"))
	}
	var credential *http.Cookie
	for _, cookie := range callback.Result().Cookies() {
		if cookie.Name == BrowserSessionCookie {
			credential = cookie
		}
	}
	if credential == nil || credential.Domain != "" || !credential.Secure || !credential.HttpOnly || credential.Path != "/" {
		t.Fatal("home cookie must be host-bound and protected")
	}
	// Login budget exhaustion must leave logout and static assets available.
	for len(home.control) < cap(home.control) {
		home.control <- struct{}{}
	}
	if w := request("GET", BrowserLoginPath); w.Code != 429 {
		t.Fatalf("login budget: %d", w.Code)
	}
	if w := request("GET", dedicatedHomePath+"/home.css"); w.Code != 200 {
		t.Fatal("assets compete for login slots")
	}
	if w := request("POST", BrowserLogoutPath, credential); w.Code != 204 || !authority.revoked.Load() {
		t.Fatalf("logout unavailable behind login budget: %d", w.Code)
	}
}
