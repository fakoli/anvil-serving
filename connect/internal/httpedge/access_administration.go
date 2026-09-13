package httpedge

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"maps"
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/administration"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

const (
	accessAdministrationPath = "/_anvil-connect/access"
	accessSchema             = "anvil-connect.access/v1"
	maxAccessBody            = 16 * 1024
	maxAccessCSRF            = 256
	accessCSRFLifetime       = 5 * time.Minute
	accessTimeout            = 5 * time.Second
)

var (
	accessRequestID = regexp.MustCompile(`\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\z`)
	accessDecimal   = regexp.MustCompile(`\A[1-9][0-9]{0,19}\z`)
	accessOpaque    = regexp.MustCompile(`\A[A-Za-z0-9._~-]{1,256}\z`)
	accessObjectID  = regexp.MustCompile(`\A[0-9a-f]{32}\z`)
)

// AccessAdministrationAuthority is the only mutable authority exposed to the
// browser edge. Its implementation must recheck the admission in its own
// transaction before listing or committing a change.
type AccessAdministrationAuthority interface {
	List(context.Context, session.Admission, string, string, int) (administration.Inventory, error)
	Mutate(context.Context, session.Admission, administration.Mutation) error
}

type accessCSRF struct {
	admitted session.Admission
	expires  time.Time
}

// AccessAdministration owns the fixed, same-origin browser adapter. It has
// neither local-admin-socket access nor credentials for an origin service.
type AccessAdministration struct {
	authority  AccessAdministrationAuthority
	resourceID string
	host       string
	path       string
	operators  map[string]bool
	slots      chan struct{}

	mu   sync.Mutex
	csrf map[string]accessCSRF
}

func NewAccessAdministration(declaration config.Gateway, authority AccessAdministrationAuthority) (*AccessAdministration, error) {
	if declaration.Validate() != nil || declaration.BrowserAdministration == nil || authority == nil {
		return nil, ErrBrowserConfiguration
	}
	settings := declaration.BrowserAdministration
	var browser config.Resource
	found := false
	for _, resource := range declaration.Resources {
		if resource.Rule.ID == settings.BrowserResource {
			browser, found = resource, true
			break
		}
	}
	if !found || browser.Rule.Access != "browser" || !browser.Rule.Allows(browser.Rule.Host, browser.Rule.PathPrefix, http.MethodGet) || !browser.Rule.Allows(browser.Rule.Host, browser.Rule.PathPrefix, http.MethodPost) {
		return nil, ErrBrowserConfiguration
	}
	path := browser.Rule.PathPrefix
	if path == "/" {
		path = accessAdministrationPath
	} else {
		path += accessAdministrationPath
	}
	capacity := browser.Rule.Limits.Concurrent
	if capacity > 4 {
		capacity = 4
	}
	operators := make(map[string]bool, len(settings.Operators))
	for _, operator := range settings.Operators {
		if !config.ValidHumanID(operator) || operators[operator] {
			return nil, ErrBrowserConfiguration
		}
		operators[operator] = true
	}
	return &AccessAdministration{authority: authority, resourceID: browser.Rule.ID, host: browser.Rule.Host, path: path, operators: operators, slots: make(chan struct{}, capacity), csrf: map[string]accessCSRF{}}, nil
}

func (a *AccessAdministration) matches(resource browserResource, path string) bool {
	return a != nil && resource.declaration.Rule.ID == a.resourceID && resource.declaration.Rule.Host == a.host && path == a.path
}

func (a *AccessAdministration) reserves(resource browserResource, path string) bool {
	return a != nil && resource.declaration.Rule.ID == a.resourceID && resource.declaration.Rule.Host == a.host && (path == a.path || strings.HasPrefix(path, a.path+"/"))
}

func (a *AccessAdministration) permits(admitted session.Admission) bool {
	return a != nil && admitted.Resource == a.resourceID && admitted.Host == a.host && a.operators[admitted.Principal]
}

func accessJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func accessFailure(w http.ResponseWriter, status int) {
	accessJSON(w, status, map[string]string{"schema": accessSchema, "error": http.StatusText(status)})
}

func sameAdmission(left, right session.Admission) bool {
	return left.SessionID == right.SessionID && left.SessionGeneration == right.SessionGeneration && left.Principal == right.Principal && left.PrincipalGeneration == right.PrincipalGeneration && left.Resource == right.Resource && left.Host == right.Host && left.Epoch == right.Epoch && left.ExpiresAt.Equal(right.ExpiresAt)
}

func (a *AccessAdministration) pruneCSRF(now time.Time) {
	for token, record := range a.csrf {
		if !now.Before(record.expires) {
			delete(a.csrf, token)
		}
	}
}

func (a *AccessAdministration) issueCSRF(admitted session.Admission) (string, bool) {
	var raw [32]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", false
	}
	token := base64.RawURLEncoding.EncodeToString(raw[:])
	now := time.Now()
	expires := now.Add(accessCSRFLifetime)
	if admitted.ExpiresAt.Before(expires) {
		expires = admitted.ExpiresAt
	}
	if !expires.After(now) {
		return "", false
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.pruneCSRF(now)
	for existing, record := range a.csrf {
		if sameAdmission(record.admitted, admitted) {
			delete(a.csrf, existing)
		}
	}
	if len(a.csrf) >= maxAccessCSRF {
		return "", false
	}
	a.csrf[token] = accessCSRF{admitted: admitted, expires: expires}
	return token, true
}

func (a *AccessAdministration) validCSRF(token string, admitted session.Admission) bool {
	if len(token) != 43 || !accessOpaque.MatchString(token) {
		return false
	}
	now := time.Now()
	a.mu.Lock()
	defer a.mu.Unlock()
	a.pruneCSRF(now)
	record, ok := a.csrf[token]
	return ok && sameAdmission(record.admitted, admitted)
}

type accessUser struct {
	ID               string            `json:"id"`
	Generation       string            `json:"generation"`
	Disabled         bool              `json:"disabled"`
	Resources        []string          `json:"resources"`
	ApplicationRoles map[string]string `json:"application_roles,omitempty"`
	Administrator    bool              `json:"administrator"`
}

type accessSession struct {
	ID            string  `json:"id"`
	Type          string  `json:"type"`
	Principal     string  `json:"principal"`
	Resource      string  `json:"resource"`
	Status        string  `json:"status"`
	IssuedAt      string  `json:"issued_at"`
	ExpiresAt     string  `json:"expires_at"`
	Generation    string  `json:"generation"`
	SourceSession *string `json:"source_session,omitempty"`
}

func accessResources(resources []string) bool {
	if len(resources) < 1 || len(resources) > 64 {
		return false
	}
	seen := map[string]bool{}
	for _, resource := range resources {
		if !config.ValidID(resource) || seen[resource] {
			return false
		}
		seen[resource] = true
	}
	return true
}

func accessApplicationRoles(resources []string, roles map[string]string) bool {
	if roles == nil || len(roles) > len(resources) {
		return false
	}
	allowed := make(map[string]bool, len(resources))
	for _, resource := range resources {
		allowed[resource] = true
	}
	for resource, role := range roles {
		if !allowed[resource] || (role != "member" && role != "admin") {
			return false
		}
	}
	return true
}

func exactAccessObject(raw json.RawMessage, expected ...string) bool {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || len(fields) != len(expected) {
		return false
	}
	for _, field := range expected {
		if _, ok := fields[field]; !ok {
			return false
		}
	}
	return true
}

func validAccessTime(value string) bool {
	if len(value) < 20 || len(value) > 64 {
		return false
	}
	_, err := time.Parse(time.RFC3339, value)
	return err == nil
}

func normalizeInventory(inventory administration.Inventory, requested string) ([]any, *string, bool) {
	if inventory.Kind != requested || len(inventory.Items) > 50 || (inventory.NextCursor != nil && !accessOpaque.MatchString(*inventory.NextCursor)) {
		return nil, nil, false
	}
	raw, err := json.Marshal(struct {
		Items []any `json:"items"`
	}{Items: inventory.Items})
	if err != nil {
		return nil, nil, false
	}
	if requested == "users" {
		var decoded struct {
			Items []accessUser `json:"items"`
		}
		if config.Decode(bytes.NewReader(raw), &decoded) != nil || len(decoded.Items) != len(inventory.Items) {
			return nil, nil, false
		}
		var objects struct {
			Items []json.RawMessage `json:"items"`
		}
		if json.Unmarshal(raw, &objects) != nil || len(objects.Items) != len(decoded.Items) {
			return nil, nil, false
		}
		result := make([]any, 0, len(decoded.Items))
		for index, item := range decoded.Items {
			fields := []string{"id", "generation", "disabled", "resources", "administrator"}
			if item.ApplicationRoles != nil {
				fields = append(fields, "application_roles")
			}
			if !exactAccessObject(objects.Items[index], fields...) || !config.ValidHumanID(item.ID) || !accessDecimal.MatchString(item.Generation) || !accessResources(item.Resources) || (item.ApplicationRoles != nil && !accessApplicationRoles(item.Resources, item.ApplicationRoles)) {
				return nil, nil, false
			}
			item.Resources = append([]string(nil), item.Resources...)
			sort.Strings(item.Resources)
			item.ApplicationRoles = maps.Clone(item.ApplicationRoles)
			result = append(result, item)
		}
		return result, inventory.NextCursor, true
	}
	var decoded struct {
		Items []accessSession `json:"items"`
	}
	if requested != "sessions" || config.Decode(bytes.NewReader(raw), &decoded) != nil || len(decoded.Items) != len(inventory.Items) {
		return nil, nil, false
	}
	var objects struct {
		Items []json.RawMessage `json:"items"`
	}
	if json.Unmarshal(raw, &objects) != nil || len(objects.Items) != len(decoded.Items) {
		return nil, nil, false
	}
	result := make([]any, 0, len(decoded.Items))
	for index, item := range decoded.Items {
		fields := []string{"id", "type", "principal", "resource", "status", "issued_at", "expires_at", "generation"}
		if item.SourceSession != nil {
			fields = append(fields, "source_session")
		}
		if !exactAccessObject(objects.Items[index], fields...) || !accessObjectID.MatchString(item.ID) || (item.Type != "browser" && item.Type != "terminal") || !config.ValidHumanID(item.Principal) || !config.ValidID(item.Resource) || (item.Status != "issued" && item.Status != "invalidated" && item.Status != "revoked" && item.Status != "expired") || !validAccessTime(item.IssuedAt) || !validAccessTime(item.ExpiresAt) || !accessDecimal.MatchString(item.Generation) || (item.SourceSession != nil && !accessObjectID.MatchString(*item.SourceSession)) {
			return nil, nil, false
		}
		result = append(result, item)
	}
	return result, inventory.NextCursor, true
}

type accessMutationInput struct {
	Action             string            `json:"action"`
	RequestID          string            `json:"request_id"`
	ExpectedGeneration string            `json:"expected_generation"`
	CSRF               string            `json:"csrf"`
	Principal          string            `json:"principal"`
	Disabled           *bool             `json:"disabled"`
	Resources          []string          `json:"resources"`
	ApplicationRoles   map[string]string `json:"application_roles"`
	SessionType        string            `json:"session_type"`
	SessionID          string            `json:"session_id"`
}

func exactMutationFields(raw []byte, expected ...string) bool {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || len(fields) != len(expected) {
		return false
	}
	for _, field := range expected {
		if _, ok := fields[field]; !ok {
			return false
		}
	}
	return true
}

func accessLimit(value string) (int, bool) {
	if !accessDecimal.MatchString(value) {
		return 0, false
	}
	limit, err := strconv.Atoi(value)
	return limit, err == nil && limit <= 50
}

func decodeAccessMutation(w http.ResponseWriter, r *http.Request, admitted session.Admission, a *AccessAdministration) (administration.Mutation, int) {
	if r.Method != http.MethodPost || browserUpgrade(r) || r.URL.RawQuery != "" || r.URL.ForceQuery || r.ContentLength < 2 || r.ContentLength > maxAccessBody || len(values(r.Header, "Content-Type")) != 1 || r.Header.Get("Content-Type") != "application/json" {
		return administration.Mutation{}, http.StatusBadRequest
	}
	if !exactBrowserOrigin(r) {
		return administration.Mutation{}, http.StatusForbidden
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxAccessBody+1))
	if err != nil || len(body) > maxAccessBody {
		return administration.Mutation{}, http.StatusBadRequest
	}
	var input accessMutationInput
	if config.Decode(bytes.NewReader(body), &input) != nil || !accessRequestID.MatchString(input.RequestID) || !accessDecimal.MatchString(input.ExpectedGeneration) {
		return administration.Mutation{}, http.StatusBadRequest
	}
	if len(values(r.Header, "X-CSRF-Token")) != 1 || values(r.Header, "X-CSRF-Token")[0] != input.CSRF || !a.validCSRF(input.CSRF, admitted) {
		return administration.Mutation{}, http.StatusForbidden
	}
	mutation := administration.Mutation{Action: input.Action, RequestID: input.RequestID, ExpectedGeneration: input.ExpectedGeneration}
	switch input.Action {
	case "human-update":
		baseFields := []string{"action", "request_id", "expected_generation", "csrf", "principal", "disabled", "resources"}
		withRoles := append(append([]string(nil), baseFields...), "application_roles")
		rolesProvided := exactMutationFields(body, withRoles...)
		if (!rolesProvided && !exactMutationFields(body, baseFields...)) || !config.ValidHumanID(input.Principal) || input.Disabled == nil || !accessResources(input.Resources) || (rolesProvided && !accessApplicationRoles(input.Resources, input.ApplicationRoles)) {
			return administration.Mutation{}, http.StatusBadRequest
		}
		mutation.Principal, mutation.Disabled, mutation.Resources = input.Principal, input.Disabled, append([]string(nil), input.Resources...)
		if rolesProvided {
			mutation.ApplicationRoles = maps.Clone(input.ApplicationRoles)
		}
	case "session-revoke":
		if !exactMutationFields(body, "action", "request_id", "expected_generation", "csrf", "session_type", "session_id") || (input.SessionType != "browser" && input.SessionType != "terminal") || !accessObjectID.MatchString(input.SessionID) {
			return administration.Mutation{}, http.StatusBadRequest
		}
		mutation.SessionType, mutation.SessionID = input.SessionType, input.SessionID
	default:
		return administration.Mutation{}, http.StatusBadRequest
	}
	return mutation, http.StatusOK
}

func (a *AccessAdministration) ServeHTTP(w http.ResponseWriter, r *http.Request, admitted session.Admission) {
	if a == nil || !a.permits(admitted) {
		accessFailure(w, http.StatusForbidden)
		return
	}
	if !acquire(a.slots) {
		accessFailure(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-a.slots }()
	ctx, cancel := context.WithTimeout(r.Context(), accessTimeout)
	defer cancel()
	r = r.WithContext(ctx)
	if r.Method == http.MethodPost {
		controller := http.NewResponseController(w)
		_ = controller.SetReadDeadline(time.Now().Add(accessTimeout))
		defer func() { _ = controller.SetReadDeadline(time.Time{}) }()
	}
	switch r.Method {
	case http.MethodGet:
		if browserUpgrade(r) || r.ContentLength != 0 {
			accessFailure(w, http.StatusBadRequest)
			return
		}
		query, err := browserQuery(r, "kind", "limit", "cursor")
		limit, validLimit := accessLimit(query.Get("limit"))
		if err != nil || !query.Has("kind") || !query.Has("limit") || query.Get("kind") != "users" && query.Get("kind") != "sessions" || !validLimit || query.Has("cursor") && !accessOpaque.MatchString(query.Get("cursor")) {
			accessFailure(w, http.StatusBadRequest)
			return
		}
		inventory, err := a.authority.List(ctx, admitted, query.Get("kind"), query.Get("cursor"), limit)
		if err != nil {
			a.accessAuthorityFailure(w, err)
			return
		}
		if ctx.Err() != nil {
			accessFailure(w, http.StatusServiceUnavailable)
			return
		}
		items, cursor, valid := normalizeInventory(inventory, query.Get("kind"))
		if !valid {
			accessFailure(w, http.StatusServiceUnavailable)
			return
		}
		csrf, ok := a.issueCSRF(admitted)
		if !ok {
			accessFailure(w, http.StatusTooManyRequests)
			return
		}
		accessJSON(w, http.StatusOK, struct {
			Schema           string  `json:"schema"`
			Kind             string  `json:"kind"`
			Items            []any   `json:"items"`
			NextCursor       *string `json:"next_cursor"`
			CSRF             string  `json:"csrf"`
			CurrentPrincipal string  `json:"current_principal"`
			CurrentSession   string  `json:"current_session"`
		}{Schema: accessSchema, Kind: query.Get("kind"), Items: items, NextCursor: cursor, CSRF: csrf, CurrentPrincipal: admitted.Principal, CurrentSession: admitted.SessionID})
	case http.MethodPost:
		mutation, status := decodeAccessMutation(w, r, admitted, a)
		if status != http.StatusOK {
			accessFailure(w, status)
			return
		}
		if err := a.authority.Mutate(ctx, admitted, mutation); err != nil {
			a.accessAuthorityFailure(w, err)
			return
		}
		if ctx.Err() != nil {
			accessFailure(w, http.StatusServiceUnavailable)
			return
		}
		accessJSON(w, http.StatusOK, struct {
			Schema    string `json:"schema"`
			Applied   bool   `json:"applied"`
			RequestID string `json:"request_id"`
		}{Schema: accessSchema, Applied: true, RequestID: mutation.RequestID})
	default:
		accessFailure(w, http.StatusNotFound)
	}
}

func (a *AccessAdministration) accessAuthorityFailure(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, administration.ErrDenied):
		accessFailure(w, http.StatusForbidden)
	case errors.Is(err, administration.ErrConflict):
		accessFailure(w, http.StatusConflict)
	case errors.Is(err, administration.ErrInvalid):
		accessFailure(w, http.StatusBadRequest)
	default:
		accessFailure(w, http.StatusServiceUnavailable)
	}
}
