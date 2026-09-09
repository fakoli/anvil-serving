// Package session owns Connect's human browser-session authority. It stores
// only opaque Connect credentials and OIDC transaction material; an IdP token
// is used to establish a session and is never persisted or forwarded.
package session

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"math"
	"net/http"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"golang.org/x/oauth2"
)

var (
	ErrDenied        = errors.New("browser session denied")
	ErrConfiguration = errors.New("invalid browser session configuration")
	ErrUnavailable   = errors.New("browser session authority unavailable")
)

const (
	DefaultTransactionLifetime = 10 * time.Minute
	DefaultSessionLifetime     = 8 * time.Hour
	MaximumSessionLifetime     = 24 * time.Hour
)

// Config identifies the one managed OIDC issuer. ClientSecret must come from
// a secret reference at the lifecycle layer; it is never written to Store.
// CallbackPath is mounted independently on every browser resource hostname.
type Config struct {
	Issuer              string
	ClientID            string
	ClientSecret        string
	CallbackPath        string
	TransactionLifetime time.Duration
	SessionLifetime     time.Duration
	MaxTransactions     int
	MaxPerBrowser       int
	HTTPClient          *http.Client
}

// Human is a locally provisioned browser principal. ID is a one-way digest of
// issuer and subject so the authority store need not retain an IdP identifier.
// A generation change invalidates every previously issued Connect session.
type Human struct {
	ID         string   `json:"id"`
	Generation uint64   `json:"generation"`
	Disabled   bool     `json:"disabled"`
	Resources  []string `json:"resources"`
}

// Session is the persisted server-side half of a host-only opaque cookie.
// Digest is a hash of the full cookie value. No IdP assertion appears here.
type Session struct {
	ID                  string    `json:"id"`
	Digest              [32]byte  `json:"digest"`
	Principal           string    `json:"principal"`
	PrincipalGeneration uint64    `json:"principal_generation"`
	Resource            string    `json:"resource"`
	Host                string    `json:"host"`
	Generation          uint64    `json:"generation"`
	Epoch               string    `json:"epoch"`
	IssuedAt            time.Time `json:"issued_at"`
	ExpiresAt           time.Time `json:"expires_at"`
	Revoked             bool      `json:"revoked"`
}

// Admission is a recheckable snapshot. httpedge must recheck it before a
// long-lived browser dispatch and must never pass it to an origin as identity.
type Admission struct {
	SessionID           string
	SessionGeneration   uint64
	Principal           string
	PrincipalGeneration uint64
	Resource            string
	Host                string
	Epoch               string
	ExpiresAt           time.Time
}

// Challenge is returned to the browser adapter. Binding is an opaque value for
// a secure host-only transaction cookie; AuthorizationURL is safe only for a
// redirect and must not be logged with its state parameter.
type Challenge struct {
	AuthorizationURL string
	Binding          string
}

// Callback carries only the bounded OIDC authorization response supplied by
// the browser adapter. Host is required to prevent a callback on a sibling
// resource hostname from consuming a valid transaction.
type Callback struct {
	Host    string
	State   string
	Code    string
	Binding string
	Issuer  string
}

// Completion returns a newly minted host-only session cookie and a confined
// local return path. The caller sets cookie attributes; this package never
// sends headers itself.
type Completion struct {
	Cookie     string
	Host       string
	Resource   string
	ReturnPath string
	ExpiresAt  time.Time
}

type transaction struct {
	State        [32]byte  `json:"state"`
	Binding      [32]byte  `json:"binding"`
	Nonce        [32]byte  `json:"nonce"`
	PKCEVerifier string    `json:"pkce_verifier"`
	Resource     string    `json:"resource"`
	Host         string    `json:"host"`
	RedirectURL  string    `json:"redirect_url"`
	ReturnPath   string    `json:"return_path"`
	IssuedAt     time.Time `json:"issued_at"`
	ExpiresAt    time.Time `json:"expires_at"`
	Epoch        string    `json:"epoch"`
}

type Manager struct {
	state                  *store.Store
	rules                  map[string]config.Rule
	issuer                 string
	responseIssuerRequired bool
	clientID               string
	clientSecret           string
	callbackPath           string
	transactionLifetime    time.Duration
	sessionLifetime        time.Duration
	maxTransactions        int
	maxPerBrowser          int
	provider               *oidc.Provider
	endpoint               oauth2.Endpoint
	client                 *http.Client
}

// New discovers the issuer and constructs a strict verifier. It deliberately
// has no insecure issuer, expiry, signature, or client-ID bypass configuration.
func New(ctx context.Context, state *store.Store, rules []config.Rule, settings Config) (*Manager, error) {
	if state == nil || !validSettings(settings) || len(rules) == 0 || len(rules) > 64 {
		return nil, ErrConfiguration
	}
	issuer, err := validatedIssuer(settings.Issuer)
	if err != nil {
		return nil, ErrConfiguration
	}
	client, err := ownedOIDCClient(settings.HTTPClient, issuer)
	if err != nil {
		return nil, ErrConfiguration
	}
	m := &Manager{
		state: state, rules: map[string]config.Rule{}, issuer: settings.Issuer,
		clientID: settings.ClientID, clientSecret: settings.ClientSecret,
		callbackPath: settings.CallbackPath, transactionLifetime: settings.TransactionLifetime,
		sessionLifetime: settings.SessionLifetime, maxTransactions: settings.MaxTransactions,
		maxPerBrowser: settings.MaxPerBrowser, client: client,
	}
	hosts := map[string]bool{}
	for _, rule := range rules {
		if rule.Validate() != nil || rule.Access != "browser" {
			m.Close()
			return nil, ErrConfiguration
		}
		if _, exists := m.rules[rule.ID]; exists || hosts[rule.Host] {
			m.Close()
			return nil, ErrConfiguration
		}
		rule.Methods = append([]string(nil), rule.Methods...)
		m.rules[rule.ID] = rule
		hosts[rule.Host] = true
	}
	ctx = oidc.ClientContext(ctx, m.client)
	ctx = context.WithValue(ctx, oauth2.HTTPClient, m.client)
	provider, err := oidc.NewProvider(ctx, m.issuer)
	if err != nil {
		m.Close()
		return nil, ErrUnavailable
	}
	m.provider = provider
	m.endpoint = provider.Endpoint()
	m.endpoint.AuthStyle = oauth2.AuthStyleInHeader
	var metadata struct {
		AuthorizationEndpoint   string `json:"authorization_endpoint"`
		TokenEndpoint           string `json:"token_endpoint"`
		JWKSURI                 string `json:"jwks_uri"`
		ResponseIssuerSupported bool   `json:"authorization_response_iss_parameter_supported"`
	}
	if provider.Claims(&metadata) != nil || !sameIssuerOrigin(issuer, metadata.AuthorizationEndpoint) || !sameIssuerOrigin(issuer, metadata.TokenEndpoint) || !sameIssuerOrigin(issuer, metadata.JWKSURI) {
		m.Close()
		return nil, ErrConfiguration
	}
	m.responseIssuerRequired = metadata.ResponseIssuerSupported
	return m, nil
}

// Close releases idle sockets opened for OIDC discovery, key refresh, and code
// exchange. It is safe to call more than once.
func (m *Manager) Close() {
	if m != nil && m.client != nil {
		if closer, ok := m.client.Transport.(interface{ CloseIdleConnections() }); ok {
			closer.CloseIdleConnections()
		}
	}
}

func validSettings(settings Config) bool {
	if _, err := validatedIssuer(settings.Issuer); err != nil || settings.ClientID == "" || len(settings.ClientID) > 256 || settings.ClientSecret == "" || len(settings.ClientSecret) > 4096 {
		return false
	}
	if !config.CanonicalPath(settings.CallbackPath) || settings.CallbackPath == "/" || strings.HasSuffix(settings.CallbackPath, "/") {
		return false
	}
	return settings.TransactionLifetime >= time.Minute && settings.TransactionLifetime <= 10*time.Minute &&
		settings.SessionLifetime >= time.Minute && settings.SessionLifetime <= MaximumSessionLifetime &&
		settings.MaxTransactions >= 1 && settings.MaxTransactions <= 128 && settings.MaxPerBrowser >= 1 && settings.MaxPerBrowser <= 8
}

func humanID(issuer, subject string) (string, bool) {
	if issuer == "" || subject == "" || len(subject) > 1024 || !utf8.ValidString(subject) {
		return "", false
	}
	digest := sha256.Sum256([]byte(issuer + "\x00" + subject))
	return "human:" + hex.EncodeToString(digest[:]), true
}

func (m *Manager) validateResources(resources []string) ([]string, bool) {
	if len(resources) < 1 || len(resources) > len(m.rules) {
		return nil, false
	}
	seen := map[string]bool{}
	copy := append([]string(nil), resources...)
	for _, resource := range copy {
		if _, exists := m.rules[resource]; !exists || seen[resource] {
			return nil, false
		}
		seen[resource] = true
	}
	sort.Strings(copy)
	return copy, true
}

// SetHuman provisions an exact issuer+subject identity. Valid OIDC assertions
// for identities absent here are denied; there is no implicit administrator.
func (m *Manager) SetHuman(issuer, subject string, resources []string, disabled bool) (Human, error) {
	if issuer != m.issuer {
		return Human{}, ErrDenied
	}
	id, ok := humanID(issuer, subject)
	if !ok {
		return Human{}, ErrDenied
	}
	resources, ok = m.validateResources(resources)
	if !ok {
		return Human{}, ErrDenied
	}
	var result Human
	err := m.state.Update(func(tx *store.Tx) error {
		var old Human
		err := tx.Get("principals", id, &old)
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return ErrUnavailable
		}
		if old.Generation == math.MaxUint64 {
			return ErrUnavailable
		}
		result = Human{ID: id, Generation: old.Generation + 1, Disabled: disabled, Resources: resources}
		return tx.Put("principals", id, result)
	})
	if err != nil {
		return Human{}, err
	}
	return result, nil
}

// NewBinding mints a 256-bit value for a host-only transaction cookie.
func NewBinding() (string, error) { return randomURLValue(32) }

func validBinding(value string) bool {
	if len(value) != 43 {
		return false
	}
	decoded, err := base64.RawURLEncoding.DecodeString(value)
	return err == nil && len(decoded) == 32 && base64.RawURLEncoding.EncodeToString(decoded) == value
}

func randomURLValue(n int) (string, error) {
	bytes := make([]byte, n)
	if _, err := rand.Read(bytes); err != nil {
		return "", ErrUnavailable
	}
	return base64.RawURLEncoding.EncodeToString(bytes), nil
}

func hashValue(value string) [32]byte { return sha256.Sum256([]byte(value)) }

func callbackURL(host, callbackPath string) string { return "https://" + host + callbackPath }

func underPrefix(prefix, requestPath string) bool {
	return prefix == "/" || requestPath == prefix || strings.HasPrefix(requestPath, prefix+"/")
}

// Begin creates a bounded transaction and returns an OIDC authorization URL.
// The supplied binding is never accepted as a session credential; it only ties
// the authorization response to the browser which started this transaction.
func (m *Manager) Begin(resource, returnPath, binding string) (Challenge, error) {
	rule, exists := m.rules[resource]
	if !exists || !validBinding(binding) || !config.CanonicalPath(returnPath) || !underPrefix(rule.PathPrefix, returnPath) {
		return Challenge{}, ErrDenied
	}
	state, err := randomURLValue(32)
	if err != nil {
		return Challenge{}, err
	}
	nonce, err := randomURLValue(32)
	if err != nil {
		return Challenge{}, err
	}
	verifier, err := randomURLValue(48)
	if err != nil {
		return Challenge{}, err
	}
	txRecord := transaction{
		State: hashValue(state), Binding: hashValue(binding), Nonce: hashValue(nonce), PKCEVerifier: verifier,
		Resource: resource, Host: rule.Host, RedirectURL: callbackURL(rule.Host, m.callbackPath), ReturnPath: returnPath,
	}
	// Transaction admission is finalized in oidc.go so it can centrally purge
	// expired records while enforcing both configured transaction caps.
	if err := m.putTransaction(txRecord); err != nil {
		return Challenge{}, err
	}
	oauth := m.oauthConfig(txRecord.RedirectURL)
	return Challenge{AuthorizationURL: oauth.AuthCodeURL(state, oauth2.S256ChallengeOption(verifier), oidc.Nonce(nonce)), Binding: binding}, nil
}

func (m *Manager) oauthConfig(redirectURL string) oauth2.Config {
	return oauth2.Config{ClientID: m.clientID, ClientSecret: m.clientSecret, Endpoint: m.endpoint, RedirectURL: redirectURL, Scopes: []string{oidc.ScopeOpenID}}
}

func sessionID(raw string) (string, bool) {
	if len(raw) != 81 || !strings.HasPrefix(raw, "acs1.") {
		return "", false
	}
	parts := strings.Split(raw, ".")
	if len(parts) != 3 || len(parts[1]) != 32 || len(parts[2]) != 43 {
		return "", false
	}
	id, err := hex.DecodeString(parts[1])
	if err != nil || hex.EncodeToString(id) != parts[1] {
		return "", false
	}
	secret, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil || len(secret) != 32 || base64.RawURLEncoding.EncodeToString(secret) != parts[2] {
		return "", false
	}
	return parts[1], true
}

func (m *Manager) authorize(tx *store.Tx, raw, host string) (Session, Human, error) {
	id, ok := sessionID(raw)
	if !ok || !config.ValidHost(host) {
		return Session{}, Human{}, ErrDenied
	}
	var session Session
	var human Human
	digest := hashValue(raw)
	if tx.Get("sessions", sessionRecord(id), &session) != nil {
		return Session{}, Human{}, ErrDenied
	}
	rule, configured := m.rules[session.Resource]
	if !configured || rule.Host != host || session.ID != id || session.Host != host || session.Revoked || session.Generation == 0 || session.Epoch != tx.Epoch() || !tx.Now().Before(session.ExpiresAt) || tx.Now().Before(session.IssuedAt) || subtle.ConstantTimeCompare(session.Digest[:], digest[:]) != 1 || tx.Get("principals", session.Principal, &human) != nil || human.ID != session.Principal || human.Disabled || human.Generation != session.PrincipalGeneration || !hasResource(human.Resources, session.Resource) {
		return Session{}, Human{}, ErrDenied
	}
	return session, human, nil
}

func hasResource(resources []string, resource string) bool {
	for _, allowed := range resources {
		if allowed == resource {
			return true
		}
	}
	return false
}

func (m *Manager) Authenticate(raw, host string) (Admission, error) {
	var admitted Admission
	err := m.state.View(func(tx *store.Tx) error {
		session, _, err := m.authorize(tx, raw, host)
		if err != nil {
			return err
		}
		admitted = Admission{SessionID: session.ID, SessionGeneration: session.Generation, Principal: session.Principal, PrincipalGeneration: session.PrincipalGeneration, Resource: session.Resource, Host: session.Host, Epoch: session.Epoch, ExpiresAt: session.ExpiresAt}
		return nil
	})
	if err != nil {
		return Admission{}, ErrDenied
	}
	return admitted, nil
}

func (m *Manager) Check(admitted Admission) error {
	if admitted.SessionID == "" || admitted.Host == "" {
		return ErrDenied
	}
	return m.state.View(func(tx *store.Tx) error {
		var session Session
		rule, configured := m.rules[admitted.Resource]
		if tx.Get("sessions", sessionRecord(admitted.SessionID), &session) != nil || !configured || rule.Host != admitted.Host || session.Generation != admitted.SessionGeneration || session.Principal != admitted.Principal || session.PrincipalGeneration != admitted.PrincipalGeneration || session.Resource != admitted.Resource || session.Host != admitted.Host || session.Epoch != admitted.Epoch || !session.ExpiresAt.Equal(admitted.ExpiresAt) {
			return ErrDenied
		}
		var human Human
		if session.Revoked || session.Generation == 0 || session.Epoch != tx.Epoch() || !tx.Now().Before(session.ExpiresAt) || tx.Now().Before(session.IssuedAt) || tx.Get("principals", session.Principal, &human) != nil || human.Disabled || human.Generation != session.PrincipalGeneration || !hasResource(human.Resources, session.Resource) {
			return ErrDenied
		}
		return nil
	})
}

// Logout invalidates every Connect session for this human across all resource
// hosts by advancing the human generation. It never touches an
// application/dashboard cookie or the Authelia federated session.
func (m *Manager) Logout(raw, host string) error {
	id, ok := sessionID(raw)
	if !ok {
		return ErrDenied
	}
	return m.state.Update(func(tx *store.Tx) error {
		session, human, err := m.authorize(tx, raw, host)
		if err != nil {
			return ErrDenied
		}
		if session.Generation == math.MaxUint64 || human.Generation == math.MaxUint64 {
			return ErrUnavailable
		}
		session.Generation++
		session.Revoked = true
		if err := tx.Put("sessions", sessionRecord(id), session); err != nil {
			return ErrUnavailable
		}
		human.Generation++
		return tx.Put("principals", human.ID, human)
	})
}
