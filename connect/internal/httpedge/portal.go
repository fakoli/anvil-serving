package httpedge

import (
	"embed"
	"html/template"
	"net/http"
	"net/url"
	"sort"
	"strings"

	"github.com/fakoli/anvil-serving/connect/internal/session"
)

//go:embed portal/*
var portalFiles embed.FS

var portalTemplate = template.Must(template.ParseFS(portalFiles, "portal/home.html"))

// These reads expose only the admitted person's current grants and the public
// account URL. Authentication and mutations retain their existing authorities.
type portalAuthority interface {
	CurrentHuman(session.Admission) (session.Human, error)
	AccountURL() string
}

type portalService struct {
	ID          string `json:"id"`
	URL         string `json:"url"`
	Role        string `json:"role"`
	ManagedRole bool   `json:"managed_role"`
}

func portalPath(resource browserResource) string {
	return strings.TrimSuffix(resource.declaration.Rule.PathPrefix, "/") + "/_anvil-connect/home"
}

func (b *Browser) portalRoute(w http.ResponseWriter, r *http.Request, resource browserResource, cookies browserCookies, home string) {
	if r.Method != http.MethodGet || !controlRequest(r) || r.URL.RawQuery != "" || r.URL.ForceQuery || len(values(r.Header, "Origin")) != 0 && !exactBrowserOrigin(r) {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
	// Static assets contain no account data and do not consume the login budget.
	assetType := map[string]string{
		home + "/home.css":    "text/css; charset=utf-8",
		home + "/home.js":     "text/javascript; charset=utf-8",
		home + "/logo.png":    "image/png",
		home + "/favicon.ico": "image/x-icon",
	}[r.URL.Path]
	if assetType != "" {
		name := strings.TrimPrefix(r.URL.Path, home+"/")
		content, err := portalFiles.ReadFile("portal/" + name)
		if err != nil {
			browserFailure(w, http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", assetType)
		_, _ = w.Write(content)
		return
	}
	release, ok := b.controlAdmission(w, resource.control, false)
	if !ok {
		return
	}
	defer release()
	admitted, ok := b.browserAdmission(r, resource, cookies)
	if !ok {
		if r.URL.Path == home && browserDocument(r) {
			browserRedirect(w, r, BrowserLoginPath+"?"+url.Values{"return": {home}}.Encode(), http.StatusFound)
		} else {
			browserFailure(w, http.StatusUnauthorized)
		}
		return
	}
	authority, ok := b.authority.(portalAuthority)
	if !ok {
		browserFailure(w, http.StatusServiceUnavailable)
		return
	}
	human, err := authority.CurrentHuman(admitted)
	if err != nil || human.ID != admitted.Principal || human.Disabled || human.Generation != admitted.PrincipalGeneration {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	switch r.URL.Path {
	case home:
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_ = portalTemplate.Execute(w, home)
	case home + "/data":
		granted := map[string]bool{}
		for _, id := range human.Resources {
			granted[id] = true
		}
		services := []portalService{}
		choices := []portalService{}
		adminPath := ""
		if b.administration != nil && b.administration.permits(admitted) {
			adminPath = b.administration.path
		}
		for _, candidate := range b.resources {
			rule := candidate.declaration.Rule
			item := portalService{ID: rule.ID, URL: "https://" + rule.Host + rule.PathPrefix, Role: human.ApplicationRoles[rule.ID], ManagedRole: rule.NativeAuth == "signed-identity"}
			if granted[rule.ID] {
				services = append(services, item)
			}
			if adminPath != "" {
				choices = append(choices, portalService{ID: rule.ID, ManagedRole: item.ManagedRole})
			}
		}
		sort.Slice(services, func(i, j int) bool { return services[i].ID < services[j].ID })
		sort.Slice(choices, func(i, j int) bool { return choices[i].ID < choices[j].ID })
		account := strings.TrimSuffix(authority.AccountURL(), "/") + "/settings"
		if !safeAuthorizationURL(account) {
			browserFailure(w, http.StatusServiceUnavailable)
			return
		}
		accessJSON(w, http.StatusOK, struct {
			Services       []portalService `json:"services"`
			Choices        []portalService `json:"choices"`
			Account        string          `json:"account_url"`
			Passkeys       string          `json:"passkeys_url"`
			Administration string          `json:"administration_path"`
			Logout         string          `json:"logout_path"`
		}{services, choices, account + "/security", account + "/two-factor-authentication", adminPath, BrowserLogoutPath})
	default:
		browserFailure(w, http.StatusNotFound)
	}
}
