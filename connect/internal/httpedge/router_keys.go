package httpedge

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/browseridentity"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

const routerKeysPath = dedicatedHomePath + "/router-keys"
const routerPrincipalPath = dedicatedHomePath + "/router-principal"

type routerAccountAuthority interface {
	RouterFence(string, string) (string, error)
	CheckRouterPrincipal(string, uint64, string) error
	RouterAccountName(string, uint64, string) (string, error)
}

type homeRouter struct {
	url         string
	checkSecret string
	signer      *browseridentity.Signer
	authority   routerAccountAuthority
	client      *http.Client
	checks      chan struct{}
	management  chan struct{}
}

func (h *Home) ConfigureRouterKeys(settings config.RouterKeys, secret, checkSecret string) error {
	authority, ok := h.authority.(routerAccountAuthority)
	if !ok || settings.Validate() != nil || h.router != nil || len(checkSecret) < 32 || len(checkSecret) > 256 || checkSecret == secret {
		return ErrBrowserConfiguration
	}
	for _, c := range checkSecret {
		if c < 33 || c > 126 {
			return ErrBrowserConfiguration
		}
	}
	signer, err := browseridentity.NewSigner(secret, "router-keys")
	if err != nil {
		return ErrBrowserConfiguration
	}
	// No ambient proxy or redirects may receive the signing assertion.
	transport := &http.Transport{MaxConnsPerHost: 4, MaxIdleConnsPerHost: 2, IdleConnTimeout: 30 * time.Second, ResponseHeaderTimeout: 4 * time.Second}
	h.router = &homeRouter{url: strings.TrimRight(settings.URL, "/"), checkSecret: checkSecret, signer: signer, authority: authority,
		client: &http.Client{Transport: transport, Timeout: 4 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, checks: make(chan struct{}, 8), management: make(chan struct{}, 2)}
	return nil
}

func routerCSRF(sessionID string) string {
	digest := sha256.Sum256([]byte("anvil-connect/router-keys\x00" + sessionID))
	return hex.EncodeToString(digest[:])
}

// routerPrincipal is a separate, bounded server-to-server lane. It never
// consumes Home browser slots (a management request itself needs this check).
func (h *Home) routerPrincipal(w http.ResponseWriter, r *http.Request) {
	if h.router == nil {
		browserFailure(w, 404)
		return
	}
	if r.Method != "POST" || r.URL.RawQuery != "" || r.URL.ForceQuery || browserUpgrade(r) || len(values(r.Header, "Origin")) != 0 || len(values(r.Header, "Cookie")) != 0 || len(values(r.Header, "Authorization")) != 1 || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+h.router.checkSecret)) != 1 {
		browserFailure(w, 403)
		return
	}
	if !acquire(h.router.checks) {
		browserFailure(w, 503)
		return
	}
	defer func() { <-h.router.checks }()
	var input struct {
		Principal  string `json:"principal"`
		Generation string `json:"generation"`
		Epoch      string `json:"epoch"`
	}
	if r.ContentLength < 2 || r.ContentLength > 1024 || r.Header.Get("Content-Type") != "application/json" {
		browserFailure(w, 400)
		return
	}
	controller := http.NewResponseController(w)
	_ = controller.SetReadDeadline(time.Now().Add(2 * time.Second))
	defer controller.SetReadDeadline(time.Time{})
	if config.Decode(http.MaxBytesReader(w, r.Body, 1024), &input) != nil || !accessDecimal.MatchString(input.Generation) {
		browserFailure(w, 400)
		return
	}
	generation, err := strconv.ParseUint(input.Generation, 10, 64)
	if err != nil {
		browserFailure(w, 400)
		return
	}
	if err = h.router.authority.CheckRouterPrincipal(input.Principal, generation, input.Epoch); err != nil {
		if errors.Is(err, session.ErrDenied) {
			browserFailure(w, 403)
		} else {
			browserFailure(w, 503)
		}
		return
	}
	h.headers(w)
	w.WriteHeader(http.StatusNoContent)
}

type routerOperation struct {
	Action      string   `json:"action"`
	Name        string   `json:"name"`
	Models      []string `json:"models"`
	Paths       []string `json:"paths"`
	RPM         int      `json:"rpm"`
	ExpiresDays int      `json:"expires_days"`
	Revision    int      `json:"revision"`
	Owner       string   `json:"owner"`
	Status      string   `json:"status"`
	KeyID       string   `json:"key_id"`
}

func (h *Home) routerKeys(w http.ResponseWriter, r *http.Request, cookies browserCookies) {
	if h.router == nil {
		browserFailure(w, 404)
		return
	}
	admitted, human, ok := h.admission(cookies.session)
	if !ok {
		browserFailure(w, 401)
		return
	}
	operator := h.operators[human.ID]
	granted := false
	for _, resource := range human.Resources {
		if resource == h.adminResource {
			granted = true
		}
	}
	operator = operator && granted
	body := []byte(`{"action":"view"}`)
	if r.Method == "GET" {
		if !homeRead(r) {
			browserFailure(w, 400)
			return
		}
	} else if r.Method == "POST" {
		if !exactBrowserOrigin(r) || r.URL.RawQuery != "" || r.URL.ForceQuery || browserUpgrade(r) || len(values(r.Header, "X-CSRF-Token")) != 1 || subtle.ConstantTimeCompare([]byte(r.Header.Get("X-CSRF-Token")), []byte(routerCSRF(admitted.SessionID))) != 1 {
			browserFailure(w, 403)
			return
		}
		if r.ContentLength < 2 || r.ContentLength > 16384 || len(values(r.Header, "Content-Type")) != 1 || r.Header.Get("Content-Type") != "application/json" {
			browserFailure(w, 400)
			return
		}
		controller := http.NewResponseController(w)
		_ = controller.SetReadDeadline(time.Now().Add(5 * time.Second))
		defer controller.SetReadDeadline(time.Time{})
		var err error
		body, err = io.ReadAll(http.MaxBytesReader(w, r.Body, 16384))
		var operation routerOperation
		if err != nil || config.Decode(bytes.NewReader(body), &operation) != nil {
			browserFailure(w, 400)
			return
		}
		if (operation.Action == "approve" || operation.Action == "forget") && !operator {
			browserFailure(w, 403)
			return
		}
	} else {
		browserFailure(w, 405)
		return
	}
	fence, err := h.router.authority.RouterFence(cookies.session, h.host)
	if err != nil {
		if errors.Is(err, session.ErrDenied) {
			browserFailure(w, 401)
		} else {
			browserFailure(w, 503)
		}
		return
	}
	upstream, err := http.NewRequestWithContext(r.Context(), "POST", h.router.url+"/v1/connect/keys", bytes.NewReader(body))
	if err != nil {
		browserFailure(w, 503)
		return
	}
	// The signed host is the authenticated Home audience; transport destination
	// remains the fixed router URL and is never supplied by the browser.
	upstream.Host = h.host
	assertion, err := h.router.signer.SignRouterKeys(admitted, upstream, operator, fence)
	if err != nil {
		browserFailure(w, 503)
		return
	}
	upstream.Header.Set(browseridentity.Header, assertion)
	upstream.Header.Set("Content-Type", "application/json")
	response, err := h.router.client.Do(upstream)
	if err != nil {
		browserFailure(w, 503)
		return
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 4*1024*1024+1))
	if err != nil || len(raw) > 4*1024*1024 || !json.Valid(raw) {
		browserFailure(w, 503)
		return
	}
	// Account names are resolved only for the freshly authorized operator view.
	// They never enter the router database or member responses.
	if r.Method == "GET" && operator && response.StatusCode == 200 {
		raw, err = h.routerAccountNames(raw)
		if err != nil {
			browserFailure(w, 503)
			return
		}
	}
	if current, _, valid := h.admission(cookies.session); !valid || !sameAdmission(current, admitted) {
		browserFailure(w, 401)
		return
	}
	h.headers(w)
	w.Header().Set("Content-Type", "application/json")
	if response.StatusCode != 200 {
		status := response.StatusCode
		if status != 400 && status != 403 && status != 429 {
			status = 503
		}
		browserFailure(w, status)
		return
	}
	// The CSRF value is session-specific and never a credential at the router.
	w.Header().Set("X-CSRF-Token", routerCSRF(admitted.SessionID))
	w.WriteHeader(200)
	_, _ = w.Write(raw)
}

func (h *Home) routerAccountNames(raw []byte) ([]byte, error) {
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return nil, err
	}
	var accounts []map[string]json.RawMessage
	if err := json.Unmarshal(envelope["accounts"], &accounts); err != nil || len(accounts) > 256 {
		return nil, ErrBrowserConfiguration
	}
	for _, account := range accounts {
		var owner, generation, fence string
		if json.Unmarshal(account["owner"], &owner) != nil || json.Unmarshal(account["generation"], &generation) != nil || json.Unmarshal(account["epoch"], &fence) != nil {
			return nil, ErrBrowserConfiguration
		}
		number, err := strconv.ParseUint(generation, 10, 64)
		if err != nil {
			return nil, err
		}
		name, err := h.router.authority.RouterAccountName(owner, number, fence)
		if err != nil && !errors.Is(err, session.ErrDenied) {
			return nil, err
		}
		available := err == nil
		if !available {
			name = ""
		}
		account["username"], _ = json.Marshal(name)
		account["available"], _ = json.Marshal(available)
	}
	envelope["accounts"], _ = json.Marshal(accounts)
	encoded, err := json.Marshal(envelope)
	if len(encoded) > 4*1024*1024 {
		return nil, ErrBrowserConfiguration
	}
	return encoded, err
}
