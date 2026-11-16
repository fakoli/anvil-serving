package httpedge

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

// A browser retrying sign-in presents its pending binding cookie; the edge
// reuses it so the authority's per-browser cap applies. A malformed cookie is
// replaced, never forwarded.
func TestLoginReusesPendingBindingCookie(t *testing.T) {
	authority := &loginCaptureAuthority{browserAuthorityStub: newBrowserAuthorityStub()}
	browser, err := NewBrowser(browserDeclaration("none"), authority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	existing, _ := session.NewBinding()
	for _, cookie := range []string{existing, "not-a-binding"} {
		r := browserRequest(http.MethodGet, BrowserLoginPath, nil)
		r.AddCookie(&http.Cookie{Name: BrowserTransactionCookie, Value: cookie})
		w := httptest.NewRecorder()
		browser.ServeHTTP(w, r)
		set := cookieValue(t, w, BrowserTransactionCookie)
		if w.Code != http.StatusFound || set == nil || set.Value != authority.binding {
			t.Fatalf("login with cookie %q = %d", cookie, w.Code)
		}
		if reused := authority.binding == cookie; reused != (cookie == existing) {
			t.Fatalf("cookie %q reused = %v", cookie, reused)
		}
	}
	if authority.clients[0] != "192.0.2.1" {
		t.Fatalf("login passed client %q, want the peer address", authority.clients[0])
	}
}

func TestClientIdentityTrustsOnlyEdgeSetPeer(t *testing.T) {
	cases := []struct {
		name, remote string
		header       map[string][]string
		want         string
	}{
		{"edge peer", "@", map[string][]string{"X-Forwarded-For": {"203.0.113.9"}}, "203.0.113.9"},
		{"direct client cannot spoof cloudflare", "@", map[string][]string{"X-Forwarded-For": {"203.0.113.9"}, "Cf-Connecting-Ip": {"198.51.100.7"}}, "203.0.113.9"},
		{"tunnel over loopback", "@", map[string][]string{"X-Forwarded-For": {"127.0.0.1"}, "Cf-Connecting-Ip": {"198.51.100.7"}}, "198.51.100.7"},
		{"tunnel without header", "@", map[string][]string{"X-Forwarded-For": {"::1"}}, "::"},
		{"ambiguous forwarded list", "192.0.2.4:443", map[string][]string{"X-Forwarded-For": {"203.0.113.9, 198.51.100.7"}}, "192.0.2.4"},
		{"repeated forwarded header", "192.0.2.4:443", map[string][]string{"X-Forwarded-For": {"203.0.113.9", "198.51.100.7"}}, "192.0.2.4"},
		{"ipv6 grouped by /56", "@", map[string][]string{"X-Forwarded-For": {"2001:db8:1:2ff:aaaa::1"}}, "2001:db8:1:200::"},
		{"unix socket without edge header", "@", nil, ""},
	}
	for _, c := range cases {
		r := httptest.NewRequest(http.MethodGet, "/", nil)
		r.RemoteAddr = c.remote
		for name, values := range c.header {
			r.Header[name] = values
		}
		if got := clientIdentity(r); got != c.want {
			t.Errorf("%s: clientIdentity = %q, want %q", c.name, got, c.want)
		}
	}
}
