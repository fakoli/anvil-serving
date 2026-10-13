package httpedge

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

func externalRoleDeclaration(t *testing.T, managed bool) config.Gateway {
	t.Helper()
	declaration := browserDeclaration("passthrough")
	if managed {
		raw, _ := json.Marshal(declaration)
		var payload map[string]any
		if err := json.Unmarshal(raw, &payload); err != nil {
			t.Fatal(err)
		}
		payload["resources"].([]any)[0].(map[string]any)["role_management"] = "external"
		raw, _ = json.Marshal(payload)
		if err := json.Unmarshal(raw, &declaration); err != nil {
			t.Fatal(err)
		}
	}
	return declaration
}

func TestExternalRolesAppearInHomeAndAccessChoices(t *testing.T) {
	for _, managed := range []bool{false, true} {
		declaration := externalRoleDeclaration(t, managed)
		authority := &portalStub{browserAuthorityStub: newBrowserAuthorityStub(), human: session.Human{ID: "human-id", Generation: 1, Resources: []string{"dash"}, ApplicationRoles: map[string]string{"dash": "admin"}}}
		browser, err := NewBrowser(declaration, authority, func(_ http.ResponseWriter, _ *http.Request, resource config.Resource, _ session.Admission) {
			if resource.Rule.NativeAuth != "passthrough" {
				t.Error("external roles changed authentication mode")
			}
		})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(browser.Close)
		browser.administration = &AccessAdministration{resourceID: "dash", host: "dash.example.test", path: "/_anvil-connect/access", operators: map[string]bool{"human-id": true}}
		read := func(handler http.Handler, host string) struct {
			Services []portalService
			Choices  []portalService
		} { t.Helper(); r := browserRequest(http.MethodGet, "/_anvil-connect/home/data", nil); r.Host = host; r.AddCookie(&http.Cookie{Name: BrowserSessionCookie, Value: "opaque-session"}); w := httptest.NewRecorder(); handler.ServeHTTP(w, r); var data struct {
			Services []portalService
			Choices  []portalService
		}; if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &data) != nil {
			t.Fatalf("portal status %d", w.Code)
		}; return data }
		portal := read(browser, "dash.example.test")
		if len(portal.Services) != 1 || len(portal.Choices) != 1 || portal.Services[0].ManagedRole != managed || portal.Choices[0].ManagedRole != managed || portal.Services[0].Role != "admin" {
			t.Fatalf("role choices wrong: %#v", portal)
		}
		declaration.PortalHost = "home.example.test"
		authority.admitted.Host, authority.admitted.Resource = declaration.PortalHost, "_connect-home"
		home, err := NewHome(declaration, &homeStub{authority})
		if err != nil {
			t.Fatal(err)
		}
		data := read(home, declaration.PortalHost)
		if len(data.Services) != 1 || data.Services[0].ManagedRole != managed || data.Services[0].Role != "admin" {
			t.Fatalf("home role wrong: %#v", data)
		}
	}
}
