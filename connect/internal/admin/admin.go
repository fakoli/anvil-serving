// Package admin exposes the native, same-UID Connect authority interface.
// Its listener is deliberately separate (localhttp); this handler never trusts
// a browser, API key, forwarded identity, or a remote network peer.
package admin

import (
	"context"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"maps"
	"math"
	"mime"
	"net/http"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

const (
	Host = "admin.anvil-connect.internal"
	Path = "/v1/admin"

	maxBody = 32 * 1024
)

var ErrAdmin = errors.New("local administrative request denied")

// Request contains only scalar administrative inputs. Operations select which
// fields are meaningful; unsupported capabilities fail closed.
type Request struct {
	Operation        string            `json:"operation"`
	Principal        string            `json:"principal"`
	Grants           []access.Grant    `json:"grants"`
	Disabled         bool              `json:"disabled"`
	KeyID            string            `json:"key_id"`
	Installation     string            `json:"installation"`
	Role             string            `json:"role"`
	Resources        []string          `json:"resources"`
	ApplicationRoles map[string]string `json:"application_roles,omitempty"`
	LifetimeSeconds  int64             `json:"lifetime_seconds"`
	Fingerprint      string            `json:"fingerprint"`
	Issuer           string            `json:"issuer"`
	Subject          string            `json:"subject"`
}

// InstallationStatus intentionally excludes public-key bodies and all prior
// key fields. Fingerprints are sufficient for a local owner to approve or
// inspect an installation.
type InstallationStatus struct {
	ID          string   `json:"id"`
	Status      string   `json:"status"`
	Fingerprint string   `json:"fingerprint"`
	Epoch       string   `json:"epoch"`
	Generation  uint64   `json:"generation"`
	Resources   []string `json:"resources"`
}

// Response returns a bearer credential only for issue and invite. It must be
// written by the native CLI to an exclusive owner-only file, never stdout.
type Response struct {
	Operation        string             `json:"operation"`
	Epoch            string             `json:"epoch"`
	Secret           string             `json:"secret"`
	KeyID            string             `json:"key_id"`
	Principal        string             `json:"principal"`
	Grants           []access.Grant     `json:"grants"`
	Invitation       string             `json:"invitation"`
	Installation     string             `json:"installation"`
	Role             string             `json:"role"`
	Resources        []string           `json:"resources"`
	ApplicationRoles map[string]string  `json:"application_roles,omitempty"`
	Generation       uint64             `json:"generation"`
	Fingerprint      string             `json:"fingerprint"`
	ControlHost      string             `json:"control_host,omitempty"`
	TunnelHost       string             `json:"tunnel_host,omitempty"`
	InnerCAPEM       string             `json:"inner_ca_pem,omitempty"`
	Status           InstallationStatus `json:"status"`
	Entries          []EntryStatus      `json:"entries,omitempty"`
}

// Handler invokes only existing authority managers. keys and sessions can be
// nil when that access profile is not configured; their operations then fail.
// Options carries the public inner-TLS bootstrap trust returned only by an
// invitation. It accepts either no values (for bounded local-only fixtures) or
// all three values; no private key material is accepted.
type ResourceReadiness struct {
	Resource      string `json:"resource"`
	Registrations int    `json:"registrations"`
}
type EntryStatus struct {
	Path      string              `json:"path"`
	Listening bool                `json:"listening"`
	Reason    string              `json:"reason"`
	Resources []ResourceReadiness `json:"resources"`
}

type Options struct {
	EntryStatus func() []EntryStatus
	ControlHost string
	TunnelHost  string
	InnerCAPEM  string
}

type Handler struct {
	state    *store.Store
	keys     *access.Keys
	identity *identity.Manager
	sessions *session.Manager
	options  Options
	slots    chan struct{}
}

func New(state *store.Store, keys *access.Keys, identities *identity.Manager, sessions *session.Manager, options ...Options) (*Handler, error) {
	if state == nil || identities == nil || len(options) > 1 {
		return nil, ErrAdmin
	}
	value := Options{}
	if len(options) == 1 {
		value = options[0]
	}
	if !validOptions(value) {
		return nil, ErrAdmin
	}
	if value.InnerCAPEM != "" {
		block, _ := pem.Decode([]byte(value.InnerCAPEM))
		certificate, _ := x509.ParseCertificate(block.Bytes)
		// Return only the parsed public certificate. Never echo ancillary PEM
		// metadata or caller-supplied formatting into an invitation bundle.
		value.InnerCAPEM = string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificate.Raw}))
	}
	return &Handler{state: state, keys: keys, identity: identities, sessions: sessions, options: value, slots: make(chan struct{}, 8)}, nil
}

func validOptions(value Options) bool {
	empty := value.ControlHost == "" && value.TunnelHost == "" && value.InnerCAPEM == ""
	if empty {
		return true
	}
	if value.ControlHost == "" || value.TunnelHost == "" || value.InnerCAPEM == "" || value.ControlHost == value.TunnelHost || !config.ValidHost(value.ControlHost) || !config.ValidHost(value.TunnelHost) || len(value.InnerCAPEM) > 64*1024 {
		return false
	}
	block, rest := pem.Decode([]byte(value.InnerCAPEM))
	if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 || len(strings.TrimSpace(string(rest))) != 0 {
		return false
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	return err == nil && certificate.IsCA && certificate.BasicConstraintsValid && certificate.KeyUsage&x509.KeyUsageCertSign != 0
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	if !validHead(request) {
		failure(w, http.StatusBadRequest)
		return
	}
	select {
	case h.slots <- struct{}{}:
		defer func() { <-h.slots }()
	default:
		failure(w, http.StatusTooManyRequests)
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 5*time.Second)
	defer cancel()
	body := relay.Body(w, http.MaxBytesReader(w, request.Body, maxBody), ctx, 5*time.Second)
	defer body.Close()
	var input Request
	if config.Decode(body, &input) != nil || ctx.Err() != nil {
		failure(w, http.StatusBadRequest)
		return
	}
	response, err := h.apply(input)
	if err != nil {
		failure(w, http.StatusForbidden)
		return
	}
	reply(w, response)
}

func validHead(request *http.Request) bool {
	if request == nil || httpedge.ValidateHead(request) != nil || request.Host != Host || request.Method != http.MethodPost || request.URL.Path != Path || request.URL.RawQuery != "" || request.URL.ForceQuery || request.ContentLength > maxBody {
		return false
	}
	for _, name := range []string{"Origin", "Cookie", "Authorization", "Proxy-Authorization", "X-Api-Key", "Upgrade"} {
		if len(request.Header.Values(name)) != 0 {
			return false
		}
	}
	for name := range request.Header {
		lower := strings.ToLower(name)
		if strings.Contains(lower, "authorization") || strings.Contains(lower, "api-key") {
			return false
		}
	}
	if len(request.Header.Values("Content-Type")) != 1 {
		return false
	}
	media, parameters, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
	return err == nil && media == "application/json" && len(parameters) <= 1 && (len(parameters) == 0 || parameters["charset"] == "utf-8")
}

func (h *Handler) apply(input Request) (Response, error) {
	if !ValidOperation(input.Operation) {
		return Response{}, ErrAdmin
	}
	response := Response{Operation: input.Operation, Grants: []access.Grant{}, Resources: []string{}, Status: InstallationStatus{Resources: []string{}}}
	var err error
	switch input.Operation {
	case "status":
		response.Epoch, err = h.epoch()
		if h.options.EntryStatus != nil {
			response.Entries = h.options.EntryStatus()
		}
	case "principal-set":
		if h.keys == nil || !config.ValidID(input.Principal) {
			return Response{}, ErrAdmin
		}
		err = h.keys.SetPrincipal(input.Principal, input.Grants, input.Disabled)
	case "api-key-issue":
		if h.keys == nil || !config.ValidID(input.Principal) {
			return Response{}, ErrAdmin
		}
		lifetime, ok := seconds(input.LifetimeSeconds)
		if !ok {
			return Response{}, ErrAdmin
		}
		var key access.Key
		response.Secret, key, err = h.keys.Issue(input.Principal, input.Grants, lifetime)
		if err == nil {
			response.KeyID, response.Principal, response.Grants, response.Epoch = key.ID, key.Principal, copyGrants(key.Grants), key.Epoch
		}
	case "api-key-revoke":
		if h.keys == nil || !keyID(input.KeyID) {
			return Response{}, ErrAdmin
		}
		err = h.keys.Revoke(input.KeyID)
	case "invite":
		if !config.ValidID(input.Installation) || (input.Role != "connector" && input.Role != "client") {
			return Response{}, ErrAdmin
		}
		lifetime, ok := seconds(input.LifetimeSeconds)
		if !ok || lifetime == 0 {
			return Response{}, ErrAdmin
		}
		var invitation identity.Invitation
		response.Invitation, invitation, err = h.identity.Invite(input.Installation, input.Role, input.Resources, lifetime)
		if err == nil {
			response.Installation, response.Role, response.Resources, response.Epoch, response.Generation = invitation.Installation, invitation.Role, append([]string(nil), invitation.Resources...), invitation.Epoch, invitation.Generation
			response.ControlHost, response.TunnelHost, response.InnerCAPEM = h.options.ControlHost, h.options.TunnelHost, h.options.InnerCAPEM
		}
	case "approve":
		if !config.ValidID(input.Installation) || !fingerprint(input.Fingerprint) {
			return Response{}, ErrAdmin
		}
		err = h.identity.Approve(input.Installation, input.Fingerprint)
	case "installation-revoke":
		if !config.ValidID(input.Installation) {
			return Response{}, ErrAdmin
		}
		err = h.identity.Revoke(input.Installation)
	case "installation-status":
		if !config.ValidID(input.Installation) {
			return Response{}, ErrAdmin
		}
		var status InstallationStatus
		status, err = h.installationStatus(input.Installation)
		if err == nil {
			response.Status = status
		}
	case "human-set":
		if h.sessions == nil || input.Issuer == "" || input.Subject == "" {
			return Response{}, ErrAdmin
		}
		var human session.Human
		human, err = h.sessions.SetHuman(input.Issuer, input.Subject, input.Resources, input.Disabled, input.ApplicationRoles)
		if err == nil {
			response.Principal, response.Generation, response.Resources, response.ApplicationRoles = human.ID, human.Generation, append([]string(nil), human.Resources...), maps.Clone(human.ApplicationRoles)
		}
	case "human-suspend":
		if h.sessions == nil || input.Issuer == "" || input.Subject == "" || input.Principal != "" || len(input.Grants) != 0 || input.Disabled || input.KeyID != "" || input.Installation != "" || input.Role != "" || len(input.Resources) != 0 || input.ApplicationRoles != nil || input.LifetimeSeconds != 0 || input.Fingerprint != "" {
			return Response{}, ErrAdmin
		}
		var human session.Human
		human, err = h.sessions.SuspendHuman(input.Issuer, input.Subject)
		if err == nil {
			response.Principal, response.Generation, response.Resources, response.ApplicationRoles = human.ID, human.Generation, append([]string(nil), human.Resources...), maps.Clone(human.ApplicationRoles)
		}
	case "human-revoke-sessions":
		if h.sessions == nil || input.Issuer == "" || input.Subject == "" || input.Principal != "" || len(input.Grants) != 0 || input.Disabled || input.KeyID != "" || input.Installation != "" || input.Role != "" || len(input.Resources) != 0 || input.ApplicationRoles != nil || input.LifetimeSeconds != 0 || input.Fingerprint != "" {
			return Response{}, ErrAdmin
		}
		var human session.Human
		human, err = h.sessions.RevokeHumanSessions(input.Issuer, input.Subject)
		if err == nil {
			response.Principal, response.Generation, response.Resources, response.ApplicationRoles = human.ID, human.Generation, append([]string(nil), human.Resources...), maps.Clone(human.ApplicationRoles)
		}
	case "authority-reset":
		err = h.state.ResetAuthority()
		if err == nil {
			response.Epoch, err = h.epoch()
		}
	}
	if err != nil {
		return Response{}, ErrAdmin
	}
	return response, nil
}

func (h *Handler) epoch() (string, error) {
	var epoch string
	err := h.state.View(func(tx *store.Tx) error {
		epoch = tx.Epoch()
		return nil
	})
	if err != nil || len(epoch) != 64 {
		return "", ErrAdmin
	}
	return epoch, nil
}

func (h *Handler) installationStatus(id string) (InstallationStatus, error) {
	var installation identity.Installation
	err := h.state.View(func(tx *store.Tx) error { return tx.Get("installations", id, &installation) })
	if err != nil || installation.ID != id || installation.Generation == 0 || len(installation.Epoch) != 64 {
		return InstallationStatus{}, ErrAdmin
	}
	return InstallationStatus{ID: installation.ID, Status: installation.Status, Fingerprint: installation.Fingerprint, Epoch: installation.Epoch, Generation: installation.Generation, Resources: append([]string(nil), installation.Resources...)}, nil
}

// ValidOperation is the closed native administrative operation vocabulary.
func ValidOperation(operation string) bool {
	switch operation {
	case "status", "principal-set", "api-key-issue", "api-key-revoke", "invite", "approve", "installation-revoke", "installation-status", "human-set", "human-suspend", "human-revoke-sessions", "authority-reset":
		return true
	default:
		return false
	}
}

func seconds(value int64) (time.Duration, bool) {
	if value < 0 || value > math.MaxInt64/int64(time.Second) {
		return 0, false
	}
	return time.Duration(value) * time.Second, true
}

func keyID(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 16 && hex.EncodeToString(decoded) == value
}

func fingerprint(value string) bool {
	if len(value) != 43 {
		return false
	}
	for _, character := range value {
		if !(character >= 'A' && character <= 'Z' || character >= 'a' && character <= 'z' || character >= '0' && character <= '9' || character == '-' || character == '_') {
			return false
		}
	}
	return true
}

func copyGrants(grants []access.Grant) []access.Grant {
	result := make([]access.Grant, len(grants))
	for index, grant := range grants {
		result[index] = access.Grant{Resource: grant.Resource, Methods: append([]string(nil), grant.Methods...)}
	}
	return result
}

func reply(w http.ResponseWriter, value Response) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_ = jsonEncoder(w, value)
}

func failure(w http.ResponseWriter, status int) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.Error(w, http.StatusText(status), status)
}

// Kept separate to make the response encoder incapable of accepting a map or
// arbitrary opaque body at this administrative boundary.
func jsonEncoder(w io.Writer, value Response) error {
	return json.NewEncoder(w).Encode(value)
}
