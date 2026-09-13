package httpedge

import (
	"bytes"
	"encoding/json"
	"image/png"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

type portalStub struct {
	*browserAuthorityStub
	human session.Human
}

func (s *portalStub) CurrentHuman(admitted session.Admission) (session.Human, error) {
	if err := s.Check(admitted); err != nil {
		return session.Human{}, err
	}
	return s.human, nil
}
func (s *portalStub) AccountURL() string { return "https://idp.example.test/" }

func TestPortalGrantsAndReservedRoutes(t *testing.T) {
	declaration := browserDeclaration("passthrough")
	declaration.Resources[0].Rule.PathPrefix = "/app"
	other := declaration.Resources[0]
	other.Rule.ID = "private-pi"
	other.Rule.Host = "pi.example.test"
	other.TunnelAddress = "127.0.0.1:17892"
	declaration.Resources = append(declaration.Resources, other)
	authority := &portalStub{browserAuthorityStub: newBrowserAuthorityStub(), human: session.Human{ID: "human-id", Generation: 1, Resources: []string{"dash"}, ApplicationRoles: map[string]string{"dash": "member"}}}
	dispatched := false
	browser, err := NewBrowser(declaration, authority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) { dispatched = true })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	home := "/app/_anvil-connect/home"
	get := func(path string, authenticated bool) *httptest.ResponseRecorder {
		r := browserRequest("GET", path, nil)
		r.Header.Set("Accept", "text/html")
		if authenticated {
			r.AddCookie(&http.Cookie{Name: BrowserSessionCookie, Value: "opaque-session"})
		}
		w := httptest.NewRecorder()
		browser.ServeHTTP(w, r)
		return w
	}
	if w := get(home, false); w.Code != 302 || !strings.Contains(w.Header().Get("Location"), "return=%2Fapp%2F_anvil-connect%2Fhome") {
		t.Fatalf("login redirect: %d", w.Code)
	}
	for _, suffix := range []string{"", "/home.css", "/home.js", "/logo.png", "/favicon.ico", "/data"} {
		w := get(home+suffix, true)
		if w.Code != 200 || w.Header().Get("Cache-Control") != "no-store" || w.Header().Get("Content-Security-Policy") == "" {
			t.Fatalf("portal %s: %d", suffix, w.Code)
		}
		if suffix == "/logo.png" {
			if _, err := png.DecodeConfig(bytes.NewReader(w.Body.Bytes())); err != nil || w.Header().Get("Content-Type") != "image/png" {
				t.Fatal("logo is not a valid served PNG")
			}
		}
		if suffix == "/favicon.ico" && (w.Header().Get("Content-Type") != "image/x-icon" || !bytes.HasPrefix(w.Body.Bytes(), []byte{0, 0, 1, 0})) {
			t.Fatal("favicon is not a served ICO")
		}
		if suffix == "" && (!strings.Contains(w.Body.String(), home+"/logo.png") || !strings.Contains(w.Header().Get("Content-Security-Policy"), "img-src 'self';")) {
			t.Fatal("logo missing from markup or denied by content security policy")
		}
		if suffix == "/data" {
			var result struct {
				Services       []portalService `json:"services"`
				Choices        []portalService `json:"choices"`
				Administration string          `json:"administration_path"`
				Account        string          `json:"account_url"`
				Passkeys       string          `json:"passkeys_url"`
			}
			if json.Unmarshal(w.Body.Bytes(), &result) != nil || len(result.Services) != 1 || result.Services[0].ID != "dash" || result.Services[0].Role != "member" || result.Services[0].URL != "https://dash.example.test/app" || len(result.Choices) != 0 || result.Administration != "" {
				t.Fatal("portal widened grants or exposed operator inventory")
			}
			if strings.Contains(w.Body.String(), "private-pi") {
				t.Fatal("hidden app leaked")
			}
			if result.Account != "https://idp.example.test/settings/security" || result.Passkeys != "https://idp.example.test/settings/two-factor-authentication" {
				t.Fatal("account actions must use settings routes without bouncing through the default landing")
			}
		}
	}
	if get(home+"/unknown", true).Code != 404 {
		t.Fatal("unknown portal route not reserved")
	}
	// All assets must load even while the single login/control slot is busy.
	resource := browser.resources["dash.example.test"]
	resource.control <- struct{}{}
	assetCodes := make(chan int, 4)
	for _, suffix := range []string{"/home.css", "/home.js", "/logo.png", "/favicon.ico"} {
		go func() { assetCodes <- get(home+suffix, false).Code }()
	}
	for range 4 {
		if code := <-assetCodes; code != 200 {
			t.Fatalf("static asset competed for login budget: %d", code)
		}
	}
	<-resource.control
	authority.revoked.Store(true)
	if get(home+"/data", true).Code != 401 {
		t.Fatal("revoked account retained portal access")
	}
	if dispatched {
		t.Fatal("portal request reached origin")
	}
}
