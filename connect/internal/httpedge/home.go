package httpedge

import (
	"context"
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

const homeSessionResource = "_connect-home"
const dedicatedHomePath = "/_anvil-connect/home"

// Home is the dedicated, grantless Connect landing host.  It owns only its
// fixed browser/OIDC endpoints and never reaches an application dispatcher.
type Home struct {
	host          string
	authority     homeAuthority
	resources     []config.Resource
	control       chan struct{}
	logout        chan struct{}
	adminURL      string
	adminResource string
	operators     map[string]bool
}

type homeAuthority interface {
	BeginPortal(string) (session.Challenge, error)
	Complete(context.Context, session.Callback) (session.Completion, error)
	PortalSession(string, string) (session.Admission, session.Human, error)
	Logout(string, string) error
	AccountURL() string
}

func NewHome(declaration config.Gateway, authority homeAuthority) (*Home, error) {
	if declaration.Validate() != nil || declaration.PortalHost == "" || !config.ValidHost(declaration.PortalHost) || authority == nil {
		return nil, ErrBrowserConfiguration
	}
	h := &Home{host: declaration.PortalHost, authority: authority, control: make(chan struct{}, min(8, declaration.MaxConcurrent)), logout: make(chan struct{}, min(8, declaration.MaxConcurrent))}
	for _, resource := range declaration.Resources {
		if resource.Rule.Access == "browser" {
			h.resources = append(h.resources, resource)
		}
	}
	if len(h.resources) == 0 {
		return nil, ErrBrowserConfiguration
	}
	if admin := declaration.BrowserAdministration; admin != nil {
		h.adminResource = admin.BrowserResource
		h.operators = make(map[string]bool, len(admin.Operators))
		for _, operator := range admin.Operators {
			h.operators[operator] = true
		}
		for _, resource := range h.resources {
			if resource.Rule.ID == admin.BrowserResource {
				h.adminURL = "https://" + resource.Rule.Host + portalPath(browserResource{declaration: resource})
				break
			}
		}
		if h.adminURL == "" {
			return nil, ErrBrowserConfiguration
		}
	}
	return h, nil
}

func (h *Home) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if ValidateHead(r) != nil || r.Host != h.host {
		browserFailure(w, http.StatusNotFound)
		return
	}
	cookies, err := parseBrowserCookies(r.Header)
	if err != nil {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	switch r.URL.Path {
	case dedicatedHomePath + "/home.css", dedicatedHomePath + "/home.js", dedicatedHomePath + "/logo.png", dedicatedHomePath + "/favicon.ico":
		h.asset(w, r)
		return
	case "/":
		if (r.Method != http.MethodGet && r.Method != http.MethodHead) || !homeRead(r) {
			browserFailure(w, http.StatusBadRequest)
			return
		}
		browserRedirect(w, r, dedicatedHomePath, http.StatusFound)
		return
	case dedicatedHomePath, dedicatedHomePath + "/data", BrowserLoginPath, BrowserCallbackPath, BrowserLogoutPath:
	default:
		browserFailure(w, http.StatusNotFound)
		return
	}
	slots := h.control
	if r.URL.Path == BrowserLogoutPath {
		slots = h.logout
	}
	if !acquire(slots) {
		browserFailure(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-slots }()
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	switch r.URL.Path {
	case dedicatedHomePath:
		h.page(w, r, cookies)
	case dedicatedHomePath + "/data":
		h.data(w, r, cookies)
	case BrowserLoginPath:
		h.login(w, r)
	case BrowserCallbackPath:
		h.callback(w, r, cookies)
	case BrowserLogoutPath:
		h.logoutRoute(w, r, cookies)
	}
}

func homeRead(r *http.Request) bool {
	return controlRequest(r) && r.URL.RawQuery == "" && !r.URL.ForceQuery && (len(values(r.Header, "Origin")) == 0 || exactBrowserOrigin(r))
}

func (h *Home) asset(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet || !homeRead(r) {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	types := map[string]string{"/home.css": "text/css; charset=utf-8", "/home.js": "text/javascript; charset=utf-8", "/logo.png": "image/png", "/favicon.ico": "image/x-icon"}
	name := strings.TrimPrefix(r.URL.Path, dedicatedHomePath+"/")
	content, err := portalFiles.ReadFile("portal/" + name)
	if err != nil {
		browserFailure(w, http.StatusNotFound)
		return
	}
	h.headers(w)
	w.Header().Set("Content-Type", types["/"+name])
	_, _ = w.Write(content)
}

func (h *Home) headers(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
}

func (h *Home) page(w http.ResponseWriter, r *http.Request, cookies browserCookies) {
	if r.Method != http.MethodGet || !homeRead(r) {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	if _, _, ok := h.admission(cookies.session); !ok {
		if browserDocument(r) {
			browserRedirect(w, r, BrowserLoginPath, http.StatusFound)
		} else {
			browserFailure(w, http.StatusUnauthorized)
		}
		return
	}
	h.headers(w)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = portalTemplate.Execute(w, dedicatedHomePath)
}

func (h *Home) admission(raw string) (session.Admission, session.Human, bool) {
	admitted, human, err := h.authority.PortalSession(raw, h.host)
	ok := err == nil && admitted.Resource == homeSessionResource && admitted.Host == h.host && admitted.SessionID != "" && admitted.Principal != "" && !admitted.ExpiresAt.IsZero() && human.ID == admitted.Principal && !human.Disabled && human.Generation == admitted.PrincipalGeneration
	return admitted, human, ok
}

func (h *Home) data(w http.ResponseWriter, r *http.Request, cookies browserCookies) {
	if r.Method != http.MethodGet || !homeRead(r) {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	admitted, human, ok := h.admission(cookies.session)
	if !ok {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	granted := map[string]bool{}
	for _, id := range human.Resources {
		granted[id] = true
	}
	adminURL := ""
	if h.operators[admitted.Principal] && granted[h.adminResource] {
		adminURL = h.adminURL
	}
	services := []portalService{}
	for _, item := range h.resources {
		if !granted[item.Rule.ID] {
			continue
		}
		service := portalService{ID: item.Rule.ID, URL: "https://" + item.Rule.Host + item.Rule.PathPrefix, Role: human.ApplicationRoles[item.Rule.ID], ManagedRole: item.Rule.NativeAuth == "signed-identity"}
		if item.DisplayName != nil {
			service.Name = *item.DisplayName
		}
		services = append(services, service)
	}
	sort.Slice(services, func(i, j int) bool { return services[i].ID < services[j].ID })
	account := strings.TrimSuffix(h.authority.AccountURL(), "/") + "/settings"
	if !safeAuthorizationURL(account) {
		browserFailure(w, http.StatusServiceUnavailable)
		return
	}
	h.headers(w)
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_ = json.NewEncoder(w).Encode(struct {
		Services       []portalService `json:"services"`
		Account        string          `json:"account_url"`
		Passkeys       string          `json:"passkeys_url"`
		Logout         string          `json:"logout_path"`
		Administration string          `json:"administration_url,omitempty"`
	}{services, account + "/security", account + "/two-factor-authentication", BrowserLogoutPath, adminURL})
}

func (h *Home) login(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet || !controlRequest(r) || r.URL.RawQuery != "" || r.URL.ForceQuery || (len(values(r.Header, "Origin")) != 0 && !exactBrowserOrigin(r)) {
		browserFailure(w, http.StatusForbidden)
		return
	}
	binding, err := session.NewBinding()
	if err != nil {
		browserFailure(w, http.StatusServiceUnavailable)
		return
	}
	challenge, err := h.authority.BeginPortal(binding)
	if err != nil || challenge.Binding != binding || !safeAuthorizationURL(challenge.AuthorizationURL) {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	setBrowserCookie(w, BrowserTransactionCookie, binding, time.Time{})
	browserRedirect(w, r, challenge.AuthorizationURL, http.StatusFound)
}

func (h *Home) callback(w http.ResponseWriter, r *http.Request, cookies browserCookies) {
	if r.Method != http.MethodGet || !controlRequest(r) || (len(values(r.Header, "Origin")) != 0 && !exactBrowserOrigin(r)) {
		browserFailure(w, http.StatusForbidden)
		return
	}
	query, err := browserQuery(r, "state", "code", "iss", "scope")
	if err != nil || !query.Has("state") || !query.Has("code") || cookies.transaction == "" || (query.Has("iss") && query.Get("iss") == "") || (query.Has("scope") && query.Get("scope") != "openid") {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	completion, err := h.authority.Complete(r.Context(), session.Callback{Host: h.host, State: query.Get("state"), Code: query.Get("code"), Binding: cookies.transaction, Issuer: query.Get("iss")})
	if err != nil || completion.Host != h.host || completion.Resource != homeSessionResource || completion.ReturnPath != dedicatedHomePath || completion.Cookie == "" || completion.ExpiresAt.IsZero() {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	setBrowserCookie(w, BrowserSessionCookie, completion.Cookie, completion.ExpiresAt)
	clearBrowserCookie(w, BrowserTransactionCookie)
	browserRedirect(w, r, dedicatedHomePath, http.StatusSeeOther)
}

func (h *Home) logoutRoute(w http.ResponseWriter, r *http.Request, cookies browserCookies) {
	if r.Method != http.MethodPost || !controlRequest(r) || !exactBrowserOrigin(r) || r.URL.RawQuery != "" || r.URL.ForceQuery {
		browserFailure(w, http.StatusForbidden)
		return
	}
	if err := h.authority.Logout(cookies.session, h.host); err != nil {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	clearBrowserCookie(w, BrowserSessionCookie)
	clearBrowserCookie(w, BrowserTransactionCookie)
	h.headers(w)
	w.WriteHeader(http.StatusNoContent)
}
