package httpedge

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"html"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/browseridentity"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/device"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

const (
	BrowserLoginPath    = "/_anvil-connect/login"
	BrowserCallbackPath = "/_anvil-connect/callback"
	BrowserLogoutPath   = "/_anvil-connect/logout"

	BrowserSessionCookie     = "__Host-anvil-connect"
	BrowserTransactionCookie = "__Host-anvil-connect-tx"
)

var ErrBrowserConfiguration = errors.New("invalid browser edge configuration")

// BrowserAuthority deliberately exposes only the session lifecycle needed by
// the HTTP edge. It does not expose OIDC credentials, human records, or store
// access to a dispatcher.
type BrowserAuthority interface {
	Begin(resource, returnPath, binding string) (session.Challenge, error)
	Complete(context.Context, session.Callback) (session.Completion, error)
	Authenticate(raw, host string) (session.Admission, error)
	Check(session.Admission) error
	Logout(raw, host string) error
}

// BrowserDispatch receives a credential-free request and an authenticated
// browser admission separately. It remains active until the response or an
// upgraded connection closes.
type BrowserDispatch func(http.ResponseWriter, *http.Request, config.Resource, session.Admission)

type browserResource struct {
	declaration config.Resource
	slots       chan struct{}
	control     chan struct{}
	device      chan struct{}
}

// Browser independently admits declared browser resources. Gateway wiring is
// intentionally separate so API and browser authentication cannot be confused.
type Browser struct {
	authority   BrowserAuthority
	active      *access.Active
	resources   map[string]browserResource
	slots       chan struct{}
	control     chan struct{}
	logoutSlots chan struct{}
	deviceSlots chan struct{}
	identities  map[string]*browseridentity.Signer
	devices     *device.Authority
	dispatch    BrowserDispatch
}

func NewBrowser(declaration config.Gateway, authority BrowserAuthority, dispatch BrowserDispatch) (*Browser, error) {
	return newBrowser(declaration, authority, nil, nil, dispatch)
}

// NewBrowserWithIdentity enables explicitly declared signed-identity browser
// resources. The caller must resolve every declared secret reference first.
func NewBrowserWithIdentity(declaration config.Gateway, authority BrowserAuthority, identities map[string]*browseridentity.Signer, dispatch BrowserDispatch) (*Browser, error) {
	return newBrowser(declaration, authority, identities, nil, dispatch)
}

// NewBrowserWithIdentityAndDevice enables both independently declared native
// identity handoff and Connect-owned device approval routes. Neither facility
// is selected by a caller header or application response.
func NewBrowserWithIdentityAndDevice(declaration config.Gateway, authority BrowserAuthority, identities map[string]*browseridentity.Signer, devices *device.Authority, dispatch BrowserDispatch) (*Browser, error) {
	return newBrowser(declaration, authority, identities, devices, dispatch)
}

func newBrowser(declaration config.Gateway, authority BrowserAuthority, identities map[string]*browseridentity.Signer, devices *device.Authority, dispatch BrowserDispatch) (*Browser, error) {
	if declaration.Validate() != nil || authority == nil || dispatch == nil || (len(declaration.DeviceAuthorizations) != 0) != (devices != nil) {
		return nil, ErrBrowserConfiguration
	}
	b := &Browser{authority: authority, resources: map[string]browserResource{}, slots: make(chan struct{}, declaration.MaxConcurrent), identities: map[string]*browseridentity.Signer{}, devices: devices, dispatch: dispatch}
	b.control = make(chan struct{}, min(8, declaration.MaxConcurrent))
	b.logoutSlots = make(chan struct{}, min(8, declaration.MaxConcurrent))
	b.deviceSlots = make(chan struct{}, min(8, declaration.MaxConcurrent))
	var err error
	b.active, err = access.NewActive(declaration.MaxConcurrent, 250*time.Millisecond)
	if err != nil {
		return nil, ErrBrowserConfiguration
	}
	for _, resource := range declaration.Resources {
		if resource.Rule.Access != "browser" {
			continue
		}
		resource.Rule.Methods = append([]string(nil), resource.Rule.Methods...)
		if resource.Rule.NativeAuth == "signed-identity" {
			signer := identities[resource.Rule.ID]
			if signer == nil {
				b.Close()
				return nil, ErrBrowserConfiguration
			}
			b.identities[resource.Rule.ID] = signer
		} else if identities != nil && identities[resource.Rule.ID] != nil {
			b.Close()
			return nil, ErrBrowserConfiguration
		}
		b.resources[resource.Rule.Host] = browserResource{declaration: resource, slots: make(chan struct{}, resource.Rule.Limits.Concurrent), control: make(chan struct{}, min(2, resource.Rule.Limits.Concurrent)), device: make(chan struct{}, min(2, resource.Rule.Limits.Concurrent))}
	}
	for resource := range identities {
		if _, exists := b.identities[resource]; !exists {
			b.Close()
			return nil, ErrBrowserConfiguration
		}
	}
	if len(b.resources) == 0 {
		b.Close()
		return nil, ErrBrowserConfiguration
	}
	return b, nil
}

func (b *Browser) Close() {
	if b != nil && b.active != nil {
		b.active.Close()
	}
}

type browserCookies struct {
	session     string
	transaction string
	native      []*http.Cookie
}

func reservedCookie(name string) bool { return strings.HasPrefix(name, BrowserSessionCookie) }

func parseBrowserCookies(header http.Header) (browserCookies, error) {
	lines := values(header, "Cookie")
	if len(lines) == 0 {
		return browserCookies{}, nil
	}
	result := browserCookies{}
	seen := map[string]bool{}
	for _, line := range lines {
		cookies, err := http.ParseCookie(line)
		if err != nil {
			return browserCookies{}, ErrRequest
		}
		for _, cookie := range cookies {
			if cookie == nil || cookie.Name == "" || seen[cookie.Name] {
				return browserCookies{}, ErrRequest
			}
			seen[cookie.Name] = true
			switch cookie.Name {
			case BrowserSessionCookie:
				result.session = cookie.Value
			case BrowserTransactionCookie:
				result.transaction = cookie.Value
			default:
				if reservedCookie(cookie.Name) {
					return browserCookies{}, ErrRequest
				}
				result.native = append(result.native, cookie)
			}
		}
	}
	return result, nil
}

func setBrowserCookie(w http.ResponseWriter, name, value string, expires time.Time) {
	http.SetCookie(w, &http.Cookie{Name: name, Value: value, Path: "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteLaxMode, Expires: expires.UTC()})
}

func clearBrowserCookie(w http.ResponseWriter, name string) {
	http.SetCookie(w, &http.Cookie{Name: name, Path: "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteLaxMode, Expires: time.Unix(1, 0).UTC(), MaxAge: -1})
}

func browserQuery(r *http.Request, allowed ...string) (url.Values, error) {
	query, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return nil, ErrRequest
	}
	permitted := map[string]bool{}
	for _, name := range allowed {
		permitted[name] = true
	}
	for name, entries := range query {
		if !permitted[name] || len(entries) != 1 {
			return nil, ErrRequest
		}
	}
	return query, nil
}

func exactBrowserOrigin(r *http.Request) bool {
	origins := values(r.Header, "Origin")
	return len(origins) == 1 && origins[0] == "https://"+r.Host
}

func browserUpgrade(r *http.Request) bool {
	return len(values(r.Header, "Upgrade")) != 0
}

func browserDocument(r *http.Request) bool {
	if r.Method != http.MethodGet || browserUpgrade(r) || len(values(r.Header, "Origin")) != 0 {
		return false
	}
	for _, accept := range values(r.Header, "Accept") {
		for _, media := range strings.Split(accept, ",") {
			if strings.EqualFold(strings.TrimSpace(strings.SplitN(media, ";", 2)[0]), "text/html") {
				return true
			}
		}
	}
	return false
}

func safeAuthorizationURL(value string) bool {
	u, err := url.Parse(value)
	return err == nil && u.Scheme == "https" && config.ValidHost(u.Host) && u.User == nil && u.Opaque == "" && u.Fragment == "" && u.RawPath == "" && config.CanonicalPath(u.Path)
}

func sameResourcePath(rule config.Rule, path string) bool {
	return config.CanonicalPath(path) && (rule.PathPrefix == "/" || path == rule.PathPrefix || strings.HasPrefix(path, rule.PathPrefix+"/"))
}

func controlRequest(r *http.Request) bool {
	return !browserUpgrade(r) && r.ContentLength == 0
}

// CleanBrowserHeaders removes Connect and proxy identity before dispatch while
// preserving native cookies and CSRF/application headers. Browser passthrough
// resources may retain at most one native Authorization value.
func CleanBrowserHeaders(header http.Header, nativeAuth string) error {
	cookies, err := parseBrowserCookies(header)
	if err != nil {
		return ErrRequest
	}
	if nativeAuth != "none" && nativeAuth != "passthrough" && nativeAuth != "signed-identity" {
		return ErrRequest
	}
	// Browser passthrough carries at most one explicit native credential. This
	// supports either conventional Authorization or X-Api-Key without allowing
	// competing application identities to reach the dashboard ambiguously.
	nativeCredentials := len(values(header, "Authorization")) + len(values(header, "X-Api-Key"))
	if nativeAuth == "passthrough" && nativeCredentials > 1 {
		return ErrRequest
	}
	for name := range header {
		lower := strings.ToLower(name)
		switch {
		case lower == "authorization", lower == "x-api-key":
			if nativeAuth != "passthrough" {
				delete(header, name)
			}
		case lower == "cookie":
			delete(header, name)
		case lower == "proxy-authorization", lower == "x-original-url", lower == "x-rewrite-url", lower == "remote-user", lower == "remote-groups", lower == "remote-email", lower == "remote-name":
			delete(header, name)
		case lower == "forwarded", strings.HasPrefix(lower, "x-forwarded-"), strings.HasPrefix(lower, "x-real-"), strings.HasPrefix(lower, "x-anvil-connect-"), strings.HasPrefix(lower, "x-auth-request-"), strings.HasPrefix(lower, "x-authenticated-"), strings.HasPrefix(lower, "cf-access-"), strings.HasPrefix(lower, "x-goog-authenticated-"), strings.HasPrefix(lower, "x-amzn-oidc-"), strings.HasPrefix(lower, "tailscale-user-"):
			delete(header, name)
		}
	}
	if len(cookies.native) != 0 {
		parts := make([]string, 0, len(cookies.native))
		for _, cookie := range cookies.native {
			parts = append(parts, cookie.String())
		}
		header.Set("Cookie", strings.Join(parts, "; "))
	}
	return nil
}

func browserFailure(w http.ResponseWriter, status int) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.Error(w, http.StatusText(status), status)
}

func browserRedirect(w http.ResponseWriter, r *http.Request, location string, status int) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.Redirect(w, r, location, status)
}

func (b *Browser) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if ValidateHead(r) != nil {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	resource, exists := b.resources[r.Host]
	if !exists {
		browserFailure(w, http.StatusNotFound)
		return
	}
	cookies, err := parseBrowserCookies(r.Header)
	if err != nil {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	if b.devices != nil {
		if approvalPath, ok := b.devices.Path(resource.declaration.Rule.ID); ok && (r.URL.Path == approvalPath || r.URL.Path == approvalPath+"/start" || r.URL.Path == approvalPath+"/poll" || r.URL.Path == approvalPath+"/cancel") {
			b.deviceRoute(w, r, resource, cookies, approvalPath)
			return
		}
	}
	switch r.URL.Path {
	case BrowserLoginPath:
		release, ok := b.controlAdmission(w, resource.control, false)
		if !ok {
			return
		}
		defer release()
		b.login(w, r, resource)
		return
	case BrowserCallbackPath:
		release, ok := b.controlAdmission(w, resource.control, false)
		if !ok {
			return
		}
		defer release()
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		r = r.WithContext(ctx)
		b.callback(w, r, resource, cookies)
		return
	case BrowserLogoutPath:
		release, ok := b.controlAdmission(w, nil, true)
		if !ok {
			return
		}
		defer release()
		b.logout(w, r, resource, cookies)
		return
	}
	if !resource.declaration.Rule.Allows(r.Host, r.URL.Path, r.Method) {
		browserFailure(w, http.StatusNotFound)
		return
	}
	if browserUpgrade(r) || len(values(r.Header, "Origin")) != 0 {
		if !exactBrowserOrigin(r) {
			browserFailure(w, http.StatusForbidden)
			return
		}
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead && r.Method != http.MethodOptions && !exactBrowserOrigin(r) {
		browserFailure(w, http.StatusForbidden)
		return
	}
	admitted, err := b.authority.Authenticate(cookies.session, r.Host)
	if err != nil {
		if browserDocument(r) {
			location := BrowserLoginPath + "?" + url.Values{"return": []string{r.URL.Path}}.Encode()
			browserRedirect(w, r, location, http.StatusFound)
			return
		}
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	if admitted.Resource != resource.declaration.Rule.ID || admitted.Host != r.Host || admitted.SessionID == "" || admitted.Principal == "" || admitted.ExpiresAt.IsZero() {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	if r.ContentLength > resource.declaration.Rule.Limits.RequestBytes {
		browserFailure(w, http.StatusRequestEntityTooLarge)
		return
	}
	if !acquire(b.slots) {
		browserFailure(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-b.slots }()
	if !acquire(resource.slots) {
		browserFailure(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-resource.slots }()
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(resource.declaration.Rule.Limits.DurationSeconds)*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		browserFailure(w, http.StatusRequestTimeout)
		return
	}
	ctx, release, err := b.active.Watch(ctx, func() error { return b.authority.Check(admitted) })
	if err != nil {
		status := http.StatusUnauthorized
		if errors.Is(err, access.ErrCapacity) {
			status = http.StatusTooManyRequests
		}
		browserFailure(w, status)
		return
	}
	defer release()
	clean := r.Clone(ctx)
	if CleanBrowserHeaders(clean.Header, resource.declaration.Rule.NativeAuth) != nil {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	clean.Body = relay.Body(w, http.MaxBytesReader(w, r.Body, resource.declaration.Rule.Limits.RequestBytes), ctx, time.Duration(resource.declaration.Rule.Limits.IdleSeconds)*time.Second)
	defer clean.Body.Close()
	if b.authority.Check(admitted) != nil {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	declaration := resource.declaration
	declaration.Rule.Methods = append([]string(nil), declaration.Rule.Methods...)
	if declaration.Rule.NativeAuth == "signed-identity" {
		assertion, signErr := b.identities[declaration.Rule.ID].Sign(admitted, declaration.Rule, clean)
		if signErr != nil {
			browserFailure(w, http.StatusServiceUnavailable)
			return
		}
		clean = clean.WithContext(browseridentity.WithAssertion(clean.Context(), assertion))
	}
	b.dispatch(w, clean, declaration, admitted)
}

// Login and callback work has a small independent budget. A consumed OIDC
// transaction does not free this budget until its code exchange completes.
// Logout uses a separate local-only budget so blocked exchanges or application
// streams cannot prevent a human from revoking Connect sessions.
func (b *Browser) controlAdmission(w http.ResponseWriter, resource chan struct{}, logout bool) (func(), bool) {
	global := b.control
	if logout {
		global = b.logoutSlots
	}
	if !acquire(global) {
		browserFailure(w, http.StatusTooManyRequests)
		return nil, false
	}
	if resource != nil && !acquire(resource) {
		<-global
		browserFailure(w, http.StatusTooManyRequests)
		return nil, false
	}
	return func() {
		if resource != nil {
			<-resource
		}
		<-global
	}, true
}

func (b *Browser) login(w http.ResponseWriter, r *http.Request, resource browserResource) {
	if r.Method != http.MethodGet || !controlRequest(r) || (len(values(r.Header, "Origin")) != 0 && !exactBrowserOrigin(r)) {
		browserFailure(w, http.StatusForbidden)
		return
	}
	query, err := browserQuery(r, "return")
	if err != nil {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	returnPath := resource.declaration.Rule.PathPrefix
	if query.Has("return") {
		returnPath = query.Get("return")
	}
	if !sameResourcePath(resource.declaration.Rule, returnPath) {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	binding, err := session.NewBinding()
	if err != nil {
		browserFailure(w, http.StatusServiceUnavailable)
		return
	}
	challenge, err := b.authority.Begin(resource.declaration.Rule.ID, returnPath, binding)
	if err != nil || challenge.Binding != binding || !safeAuthorizationURL(challenge.AuthorizationURL) {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	setBrowserCookie(w, BrowserTransactionCookie, binding, time.Time{})
	browserRedirect(w, r, challenge.AuthorizationURL, http.StatusFound)
}

func (b *Browser) callback(w http.ResponseWriter, r *http.Request, resource browserResource, cookies browserCookies) {
	if r.Method != http.MethodGet || !controlRequest(r) || (len(values(r.Header, "Origin")) != 0 && !exactBrowserOrigin(r)) {
		browserFailure(w, http.StatusForbidden)
		return
	}
	query, err := browserQuery(r, "state", "code", "iss", "scope")
	if err != nil || !query.Has("state") || !query.Has("code") || cookies.transaction == "" || (query.Has("iss") && query.Get("iss") == "") || (query.Has("scope") && query.Get("scope") != "openid") {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	completion, err := b.authority.Complete(r.Context(), session.Callback{Host: r.Host, State: query.Get("state"), Code: query.Get("code"), Binding: cookies.transaction, Issuer: query.Get("iss")})
	if err != nil || completion.Host != r.Host || completion.Resource != resource.declaration.Rule.ID || !sameResourcePath(resource.declaration.Rule, completion.ReturnPath) || completion.Cookie == "" || completion.ExpiresAt.IsZero() {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	setBrowserCookie(w, BrowserSessionCookie, completion.Cookie, completion.ExpiresAt)
	clearBrowserCookie(w, BrowserTransactionCookie)
	browserRedirect(w, r, completion.ReturnPath, http.StatusSeeOther)
}

func (b *Browser) logout(w http.ResponseWriter, r *http.Request, resource browserResource, cookies browserCookies) {
	if r.Method != http.MethodPost || !controlRequest(r) || !exactBrowserOrigin(r) || r.URL.RawQuery != "" || r.URL.ForceQuery {
		browserFailure(w, http.StatusForbidden)
		return
	}
	if err := b.authority.Logout(cookies.session, r.Host); err != nil {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	clearBrowserCookie(w, BrowserSessionCookie)
	clearBrowserCookie(w, BrowserTransactionCookie)
	if resource.declaration.Rule.NativeAuth == "signed-identity" {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("<!doctype html><title>Signed out</title><p>Signed out.</p><p><a href=\"" + html.EscapeString(resource.declaration.Rule.PathPrefix) + "\">Sign in again</a></p>"))
		return
	}
	browserRedirect(w, r, resource.declaration.Rule.PathPrefix, http.StatusSeeOther)
}

// ValidateBrowserResponse preserves native response headers and bodies while
// refusing only cookie and redirect behavior that can escape this resource.
// Callers should turn an error into an upstream failure before copying headers.
func ValidateBrowserResponse(response *http.Response, rule config.Rule) error {
	if response == nil || rule.Validate() != nil || rule.Access != "browser" {
		return ErrRequest
	}
	if len(response.Header.Values(browseridentity.Header)) != 0 {
		return ErrRequest
	}
	locations := response.Header.Values("Location")
	if len(locations) > 1 {
		return ErrRequest
	}
	if len(locations) == 1 {
		u, err := url.Parse(locations[0])
		if err != nil || u.User != nil || u.Opaque != "" || u.RawPath != "" || u.Fragment != "" || strings.HasPrefix(locations[0], "//") {
			return ErrRequest
		}
		if u.IsAbs() && (u.Scheme != "https" || u.Host != rule.Host) {
			return ErrRequest
		}
		if !sameResourcePath(rule, u.Path) {
			return ErrRequest
		}
	}
	for _, value := range response.Header.Values("Set-Cookie") {
		cookie, err := http.ParseSetCookie(value)
		if err != nil || cookie == nil || cookie.Domain != "" || reservedCookie(cookie.Name) {
			return ErrRequest
		}
		for _, attribute := range strings.Split(value, ";")[1:] {
			if strings.EqualFold(strings.TrimSpace(strings.SplitN(attribute, "=", 2)[0]), "domain") {
				return ErrRequest
			}
		}
	}
	return nil
}

type deviceRequest struct {
	DeviceCode string `json:"device_code"`
}

func deviceJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func readDeviceRequest(w http.ResponseWriter, r *http.Request) (deviceRequest, bool) {
	if r.Method != http.MethodPost || r.URL.RawQuery != "" || r.URL.ForceQuery || r.ContentLength < 2 || r.ContentLength > 512 || len(values(r.Header, "Content-Type")) != 1 || r.Header.Get("Content-Type") != "application/json" {
		return deviceRequest{}, false
	}
	body, err := io.ReadAll(io.LimitReader(http.MaxBytesReader(w, r.Body, 513), 513))
	if err != nil || len(body) > 512 {
		return deviceRequest{}, false
	}
	var input deviceRequest
	if config.Decode(bytes.NewReader(body), &input) != nil || input.DeviceCode == "" || len(input.DeviceCode) > 128 {
		return deviceRequest{}, false
	}
	return input, true
}

func (b *Browser) deviceRoute(w http.ResponseWriter, r *http.Request, resource browserResource, cookies browserCookies, approvalPath string) {
	if b.devices == nil || browserUpgrade(r) || r.URL.Path == "" {
		browserFailure(w, http.StatusNotFound)
		return
	}
	if !acquire(b.deviceSlots) {
		browserFailure(w, http.StatusTooManyRequests)
		return
	}
	if !acquire(resource.device) {
		<-b.deviceSlots
		browserFailure(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-resource.device; <-b.deviceSlots }()
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	if r.Method == http.MethodPost {
		controller := http.NewResponseController(w)
		_ = controller.SetReadDeadline(time.Now().Add(5 * time.Second))
		defer func() { _ = controller.SetReadDeadline(time.Time{}) }()
	}
	if ctx.Err() != nil {
		browserFailure(w, http.StatusRequestTimeout)
		return
	}
	if b.devices == nil || browserUpgrade(r) || r.URL.Path == "" {
		browserFailure(w, http.StatusNotFound)
		return
	}
	binding, ok := b.devices.Binding(resource.declaration.Rule.ID)
	if !ok || binding.Browser.Host != r.Host || approvalPath == "" {
		browserFailure(w, http.StatusNotFound)
		return
	}
	switch r.URL.Path {
	case approvalPath + "/start":
		if r.Method != http.MethodPost || r.ContentLength != 0 || r.URL.RawQuery != "" || r.URL.ForceQuery || len(values(r.Header, "Origin")) != 0 {
			browserFailure(w, http.StatusForbidden)
			return
		}
		started, err := b.devices.Start(resource.declaration.Rule.ID)
		if err != nil {
			browserFailure(w, http.StatusTooManyRequests)
			return
		}
		deviceJSON(w, http.StatusOK, map[string]any{"status": "pending", "device_code": started.DeviceCode, "user_code": started.UserCode, "expires_in": int(time.Until(started.ExpiresAt).Seconds()), "interval": int(device.PollInterval.Seconds())})
		return
	case approvalPath + "/poll", approvalPath + "/cancel":
		input, valid := readDeviceRequest(w, r)
		if !valid {
			browserFailure(w, http.StatusBadRequest)
			return
		}
		if r.URL.Path == approvalPath+"/cancel" {
			if b.devices.Cancel(input.DeviceCode) != nil {
				browserFailure(w, http.StatusUnauthorized)
				return
			}
			deviceJSON(w, http.StatusOK, map[string]string{"status": "cancelled"})
			return
		}
		polled, err := b.devices.Poll(input.DeviceCode)
		if errors.Is(err, device.ErrSlowDown) {
			deviceJSON(w, http.StatusTooManyRequests, map[string]any{"status": "slow_down", "interval": int(device.PollInterval.Seconds())})
			return
		}
		if err != nil {
			browserFailure(w, http.StatusUnauthorized)
			return
		}
		if polled.Secret != "" {
			deviceJSON(w, http.StatusOK, map[string]any{"status": "approved", "access_token": polled.Secret, "expires_in": int(time.Until(polled.ExpiresAt).Seconds())})
			return
		}
		deviceJSON(w, http.StatusOK, map[string]any{"status": polled.Status, "interval": int(device.PollInterval.Seconds())})
		return
	case approvalPath:
		b.deviceApproval(w, r, resource, cookies, binding)
		return
	default:
		browserFailure(w, http.StatusNotFound)
	}
}

func (b *Browser) deviceAdmission(w http.ResponseWriter, r *http.Request, resource browserResource, cookies browserCookies) (session.Admission, bool) {
	admitted, err := b.authority.Authenticate(cookies.session, r.Host)
	if err != nil || admitted.Resource != resource.declaration.Rule.ID || admitted.Host != r.Host || admitted.SessionID == "" || admitted.Principal == "" || admitted.ExpiresAt.IsZero() || b.authority.Check(admitted) != nil {
		return session.Admission{}, false
	}
	return admitted, true
}

func (b *Browser) deviceApproval(w http.ResponseWriter, r *http.Request, resource browserResource, cookies browserCookies, binding device.Binding) {
	if r.Method == http.MethodGet && r.URL.RawQuery == "" && !r.URL.ForceQuery && !browserUpgrade(r) {
		admitted, ok := b.deviceAdmission(w, r, resource, cookies)
		if !ok {
			if browserDocument(r) {
				browserRedirect(w, r, BrowserLoginPath+"?"+url.Values{"return": []string{r.URL.Path}}.Encode(), http.StatusFound)
			} else {
				browserFailure(w, http.StatusUnauthorized)
			}
			return
		}
		csrf := b.devices.CSRF(admitted, binding)
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = io.WriteString(w, "<!doctype html><title>Approve device</title><h1>Approve device</h1><p>"+html.EscapeString(binding.Label)+" requests "+html.EscapeString(binding.API.ID)+" ("+html.EscapeString(strings.Join(binding.Methods, ", "))+")</p><form method=post><label>Code <input name=user_code autocomplete=one-time-code required></label><input type=hidden name=csrf value=\""+html.EscapeString(csrf)+"\"><button name=decision value=approve>Approve</button><button name=decision value=deny>Deny</button></form>")
		return
	}
	if r.Method != http.MethodPost || !exactBrowserOrigin(r) || r.URL.RawQuery != "" || r.URL.ForceQuery || r.ContentLength < 1 || r.ContentLength > 512 || len(values(r.Header, "Content-Type")) != 1 || r.Header.Get("Content-Type") != "application/x-www-form-urlencoded" {
		browserFailure(w, http.StatusForbidden)
		return
	}
	admitted, ok := b.deviceAdmission(w, r, resource, cookies)
	if !ok {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 513)
	if r.ParseForm() != nil || len(r.PostForm) != 3 || len(r.PostForm["user_code"]) != 1 || len(r.PostForm["decision"]) != 1 || len(r.PostForm["csrf"]) != 1 || len(r.PostForm["user_code"][0]) != 8 || (r.PostForm["decision"][0] != "approve" && r.PostForm["decision"][0] != "deny") || !b.devices.ValidCSRF(admitted, binding, r.PostForm["csrf"][0]) {
		browserFailure(w, http.StatusBadRequest)
		return
	}
	if _, err := b.devices.Approve(admitted, r.PostForm["user_code"][0], r.PostForm["decision"][0] == "approve"); err != nil {
		browserFailure(w, http.StatusUnauthorized)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = io.WriteString(w, "<!doctype html><title>Device decision recorded</title><p>Device decision recorded.</p>")
}
