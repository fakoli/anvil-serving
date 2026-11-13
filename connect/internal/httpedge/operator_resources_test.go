package httpedge

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

func operatorDeclaration() config.Gateway {
	declaration := browserDeclaration("none")
	declaration.BrowserAdministration = &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{"human:" + strings.Repeat("a", 64)}}
	restricted := true
	application := declaration.Resources[0]
	application.Rule.ID, application.Rule.Host = "admin-app", "admin-app.example.test"
	application.TunnelAddress, application.OperatorOnly = "127.0.0.1:17892", &restricted
	name := "Administrative application"
	application.DisplayName = &name
	declaration.Resources = append(declaration.Resources, application)
	return declaration
}

func TestOperatorApplicationBlocksDirectRequestsBeforeOrigin(t *testing.T) {
	declaration := operatorDeclaration()
	for _, operator := range []bool{false, true} {
		t.Run(map[bool]string{false: "nonoperator-with-app-admin-grant", true: "operator"}[operator], func(t *testing.T) {
			authority := newBrowserAuthorityStub()
			authority.admitted.Resource, authority.admitted.Host = "admin-app", "admin-app.example.test"
			authority.admitted.ApplicationRole = "admin"
			if operator {
				authority.admitted.Principal = declaration.BrowserAdministration.Operators[0]
			}
			dispatches := 0
			browser, err := NewBrowser(declaration, authority, func(w http.ResponseWriter, r *http.Request, resource config.Resource, admitted session.Admission) {
				dispatches++
				if r.Host != "admin-app.example.test" || r.Header.Get("Origin") != "https://admin-app.example.test" || r.Header.Get("Cookie") != "" || r.Header.Get("X-Forwarded-User") != "" {
					t.Error("origin authority or identity header boundary changed")
				}
				w.WriteHeader(http.StatusNoContent)
			})
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(browser.Close)
			request := func(method, path, origin, cookie string, upgrade bool) *httptest.ResponseRecorder {
				r := browserRequest(method, path, nil)
				r.Host = "admin-app.example.test"
				if origin != "" {
					r.Header.Set("Origin", origin)
				}
				if cookie != "" {
					r.AddCookie(&http.Cookie{Name: BrowserSessionCookie, Value: cookie})
				}
				r.Header.Set("X-Forwarded-User", "operator")
				if upgrade {
					r.Header.Set("Connection", "Upgrade")
					r.Header.Set("Upgrade", "websocket")
				}
				w := httptest.NewRecorder()
				browser.ServeHTTP(w, r)
				return w
			}
			for _, method := range []string{"GET", "POST"} {
				w := request(method, "/api/projects", "https://admin-app.example.test", "opaque-session", false)
				want := http.StatusForbidden
				if operator {
					want = http.StatusNoContent
				}
				if w.Code != want {
					t.Fatalf("%s: got %d want %d", method, w.Code, want)
				}
			}
			if !operator {
				if w := request("GET", "/stream", "https://admin-app.example.test", "opaque-session", true); w.Code != http.StatusForbidden {
					t.Fatalf("nonoperator upgrade: %d", w.Code)
				}
				if w := request("GET", dedicatedHomePath+"/data", "", "opaque-session", false); w.Code != http.StatusForbidden {
					t.Fatalf("nonoperator app home: %d", w.Code)
				}
			}
			before := dispatches
			if request("GET", "/api/projects", "", "", false).Code != http.StatusUnauthorized || request("POST", "/api/projects", "https://foreign.example.test", "opaque-session", false).Code != http.StatusForbidden {
				t.Fatal("anonymous or cross-origin request admitted")
			}
			authority.revoked.Store(true)
			if request("GET", "/api/projects", "", "opaque-session", false).Code != http.StatusUnauthorized || dispatches != before || !operator && dispatches != 0 {
				t.Fatal("refused request reached origin")
			}
		})
	}
}

func TestOperatorApplicationCatalogRetainsExplicitGrants(t *testing.T) {
	declaration := operatorDeclaration()
	declaration.PortalHost = "home.example.test"
	for _, dedicated := range []bool{false, true} {
		for _, operator := range []bool{false, true} {
			authority := &portalStub{browserAuthorityStub: newBrowserAuthorityStub(), human: session.Human{Generation: 1, Resources: []string{"dash", "admin-app"}, ApplicationRoles: map[string]string{"admin-app": "admin"}}}
			if operator {
				authority.admitted.Principal = declaration.BrowserAdministration.Operators[0]
			}
			authority.human.ID = authority.admitted.Principal
			var handler http.Handler
			if dedicated {
				authority.admitted.Host, authority.admitted.Resource = declaration.PortalHost, homeSessionResource
				home, err := NewHome(declaration, &homeStub{authority})
				if err != nil {
					t.Fatal(err)
				}
				handler = home
			} else {
				browser, err := NewBrowser(declaration, authority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {
					t.Error("catalog dispatched origin")
				})
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(browser.Close)
				browser.administration = &AccessAdministration{resourceID: "dash", host: "dash.example.test", path: accessAdministrationPath, operators: map[string]bool{declaration.BrowserAdministration.Operators[0]: true}}
				handler = browser
			}
			read := func() []portalService {
				r := browserRequest("GET", dedicatedHomePath+"/data", nil)
				r.Host = authority.admitted.Host
				r.AddCookie(&http.Cookie{Name: BrowserSessionCookie, Value: "opaque-session"})
				w := httptest.NewRecorder()
				handler.ServeHTTP(w, r)
				var data struct{ Services, Choices []portalService }
				if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &data) != nil {
					t.Fatalf("catalog: %d", w.Code)
				}
				for _, choice := range data.Choices {
					if choice.ID == "admin-app" {
						t.Fatal("operator-only app offered for arbitrary access grants")
					}
				}
				return data.Services
			}
			services := read()
			want := 1
			if operator {
				want = 2
			}
			if len(services) != want {
				t.Fatalf("dedicated=%v operator=%v catalog=%#v", dedicated, operator, services)
			}
			authority.human.Resources = []string{"dash"}
			if services = read(); len(services) != 1 || services[0].ID != "dash" {
				t.Fatal("operator status granted an implicit app entitlement")
			}
		}
	}
}
