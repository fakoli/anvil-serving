package httpedge

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/browseridentity"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

type routerHomeStub struct {
	*homeStub
	fenceError, checkError error
}

func (s *routerHomeStub) RouterFence(raw, host string) (string, error) {
	if s.fenceError != nil {
		return "", s.fenceError
	}
	if _, _, err := s.PortalSession(raw, host); err != nil {
		return "", err
	}
	return strings.Repeat("f", 64), nil
}
func (s *routerHomeStub) CheckRouterPrincipal(id string, generation uint64, epoch string) error {
	if s.checkError != nil {
		return s.checkError
	}
	if s.human.Disabled || id != s.human.ID || generation != s.human.Generation || epoch != strings.Repeat("f", 64) {
		return session.ErrDenied
	}
	return nil
}

func TestHomeRouterBoundaryAndSignedIdentity(t *testing.T) {
	declaration := browserDeclaration("none")
	declaration.PortalHost = "home.example.test"
	principal := "human:" + strings.Repeat("a", 64)
	declaration.BrowserAdministration = &config.BrowserAdministration{BrowserResource: "dash", Operators: []string{principal}}
	authority := &routerHomeStub{homeStub: &homeStub{&portalStub{browserAuthorityStub: newBrowserAuthorityStub(), human: session.Human{ID: principal, Generation: 1, Resources: []string{"dash"}}}}}
	authority.admitted = session.Admission{Principal: principal, PrincipalGeneration: 1, SessionID: strings.Repeat("1", 32), SessionGeneration: 1, Resource: homeSessionResource, Host: declaration.PortalHost, Epoch: strings.Repeat("e", 64), ExpiresAt: time.Now().Add(time.Hour)}
	calls := 0
	role := ""
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != "POST" || r.URL.Path != "/v1/connect/keys" || r.Header.Get("Cookie") != "" || r.Header.Get("Authorization") != "" {
			t.Error("forwarded caller state")
		}
		token := r.Header.Get(browseridentity.Header)
		if !browseridentity.ValidWire(token) {
			t.Error("unsigned broker call")
		}
		raw, err := base64.RawURLEncoding.DecodeString(strings.Split(token, ".")[1])
		if err != nil {
			t.Fatal(err)
		}
		var claims map[string]any
		if json.Unmarshal(raw, &claims) != nil {
			t.Fatal("claims")
		}
		if claims["sub"] != principal || claims["epoch"] != strings.Repeat("f", 64) || claims["resource"] != "router-keys" {
			t.Error("wrong delegated identity")
		}
		role, _ = claims["role"].(string)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"account":null,"keys":[],"usage":[]}`))
	}))
	defer broker.Close()
	home, err := NewHome(declaration, authority)
	if err != nil {
		t.Fatal(err)
	}
	secret := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("s", 32)))
	check := strings.Repeat("c", 43)
	if home.ConfigureRouterKeys(config.RouterKeys{URL: broker.URL, SecretEnv: "SIGNING", CheckEnv: "CHECK"}, secret, check) != nil {
		t.Fatal("configure")
	}
	request := func(method, path, body, csrf, origin, cookie, bearer string) *httptest.ResponseRecorder {
		r := browserRequest(method, path, strings.NewReader(body))
		r.Host = declaration.PortalHost
		if body != "" {
			r.Header.Set("Content-Type", "application/json")
		}
		if csrf != "" {
			r.Header.Set("X-CSRF-Token", csrf)
		}
		if origin != "" {
			r.Header.Set("Origin", origin)
		}
		if cookie != "" {
			r.AddCookie(&http.Cookie{Name: BrowserSessionCookie, Value: cookie})
		}
		if bearer != "" {
			r.Header.Set("Authorization", "Bearer "+bearer)
		}
		w := httptest.NewRecorder()
		home.ServeHTTP(w, r)
		return w
	}
	read := request("GET", routerKeysPath, "", "", "", "opaque-session", "")
	if read.Code != 200 || role != "admin" || read.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("view: %d", read.Code)
	}
	csrf := read.Header().Get("X-CSRF-Token")
	for _, test := range []struct {
		body, csrf, origin, cookie string
		status                     int
	}{
		{`{"action":"request"}`, csrf, "https://home.example.test", "opaque-session", 200},
		{`{"action":"request"}`, "wrong", "https://home.example.test", "opaque-session", 403},
		{`{"action":"request"}`, csrf, "https://evil.example.test", "opaque-session", 403},
		{`{"action":"request","principal":"spoof"}`, csrf, "https://home.example.test", "opaque-session", 400},
		{`{"action":"request","action":"view"}`, csrf, "https://home.example.test", "opaque-session", 400},
		{`{"action":"request"}`, csrf, "https://home.example.test", "", 401},
	} {
		if w := request("POST", routerKeysPath, test.body, test.csrf, test.origin, test.cookie, ""); w.Code != test.status {
			t.Fatalf("boundary: %d want %d", w.Code, test.status)
		}
	}
	authority.human.Resources = nil
	if w := request("GET", routerKeysPath, "", "", "", "opaque-session", ""); w.Code != 200 || role != "member" {
		t.Fatal("browser grant removal did not remove admin authority")
	}
	before := calls
	if w := request("POST", routerKeysPath, `{"action":"approve"}`, csrf, "https://home.example.test", "opaque-session", ""); w.Code != 403 || calls != before {
		t.Fatal("member approval reached broker")
	}
	payload := `{"principal":"` + principal + `","generation":"1","epoch":"` + strings.Repeat("f", 64) + `"}`
	if w := request("POST", routerPrincipalPath, payload, "", "", "", check); w.Code != 204 {
		t.Fatalf("principal check %d", w.Code)
	}
	if w := request("POST", routerPrincipalPath, payload, "", "", "", secret); w.Code != 403 {
		t.Fatal("signing credential accepted as check bearer")
	}

	for _, failure := range []struct {
		err                    error
		checkStatus, keyStatus int
	}{{session.ErrUnavailable, 503, 503}, {session.ErrDenied, 403, 401}} {
		authority.checkError, authority.fenceError = failure.err, failure.err
		if w := request("POST", routerPrincipalPath, payload, "", "", "", check); w.Code != failure.checkStatus {
			t.Fatalf("check error: %d", w.Code)
		}
		if w := request("GET", routerKeysPath, "", "", "", "opaque-session", ""); w.Code != failure.keyStatus {
			t.Fatalf("fence error: %d", w.Code)
		}
	}
	authority.checkError, authority.fenceError = nil, nil
	for acquire(home.router.management) {
	}
	if w := request("GET", routerKeysPath, "", "", "", "opaque-session", ""); w.Code != 429 {
		t.Fatal("management capacity not bounded")
	}
	if w := request("GET", dedicatedHomePath+"/data", "", "", "", "opaque-session", ""); w.Code != 200 {
		t.Fatalf("management blocked Home: %d", w.Code)
	}
	if len(home.control) != 0 {
		t.Fatal("management consumed control slots")
	}
	for len(home.router.management) > 0 {
		<-home.router.management
	}
	authority.human.Disabled = true
	if w := request("POST", routerPrincipalPath, payload, "", "", "", check); w.Code != 403 {
		t.Fatal("disabled principal accepted")
	}
}
