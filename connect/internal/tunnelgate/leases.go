// Package tunnelgate authorizes and owns every external tunnel Upgrade before
// handing its raw stream to the private, independently restricted OSS tunnel.
package tunnelgate

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
)

var ErrDenied = errors.New("tunnel access denied")

const TransportLeaseLifetime = time.Minute
const maximumTokens = 1024

type InstallationAuthority interface {
	Check(identity.Installation, string) error
}

// Admission contains no credential. The private group identifier ties a stream
// to exactly the installation key/generation which obtained its admission.
type Admission struct {
	Resource    string
	group       string
	incarnation string
}

type transportToken struct {
	digest [32]byte
	group  string
	issued time.Time
	until  time.Time
}

type permission struct {
	installation identity.Installation
	resource     string
	issued       time.Time
	until        time.Time
	incarnation  string
}

// Leases is volatile. Restart invalidates every transport bearer. Control
// refreshes mint new admission tokens; existing streams follow the renewable
// permission for their exact installation epoch, generation and public key.
// Refreshing one generation never extends another generation's streams.
type Leases struct {
	mu        sync.Mutex
	authority InstallationAuthority
	routes    map[string]config.Resource
	now       func() time.Time
	tokens    map[string]transportToken
	groups    map[string]permission
}

func NewLeases(declaration config.Gateway, authority InstallationAuthority, now func() time.Time) (*Leases, error) {
	if declaration.Validate() != nil || authority == nil {
		return nil, ErrDenied
	}
	if now == nil {
		now = time.Now
	}
	l := &Leases{authority: authority, now: now, routes: map[string]config.Resource{}, tokens: map[string]transportToken{}, groups: map[string]permission{}}
	for _, resource := range declaration.Resources {
		resource.Rule.Methods = append([]string(nil), resource.Rule.Methods...)
		l.routes[resource.Rule.ID] = resource
	}
	return l, nil
}

func groupID(i identity.Installation, resource string) string {
	// Hash a length-unambiguous struct encoding, not delimiter-separated input.
	// PublicKey is canonical in the verified installation record.
	value := struct {
		ID, Role, Epoch, Fingerprint, Key, Resource string
		Generation                                  uint64
	}{i.ID, i.Role, i.Epoch, i.Fingerprint, string(i.PublicKey), resource, i.Generation}
	encoded, _ := json.Marshal(value)
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

// Issue accepts ONLY the snapshot returned by identity.Manager.Verify for a
// fresh server challenge. It is never an HTTP endpoint and must not receive
// caller-decoded installation JSON. The control layer consumes the proof first.
func (l *Leases) Issue(installation identity.Installation, resource string) (string, time.Time, error) {
	route, exists := l.routes[resource]
	if !exists || installation.Role != "connector" || installation.ID != route.Connector || l.authority.Check(installation, resource) != nil {
		return "", time.Time{}, ErrDenied
	}
	var id [16]byte
	var secret [32]byte
	if _, err := rand.Read(id[:]); err != nil {
		return "", time.Time{}, ErrDenied
	}
	if _, err := rand.Read(secret[:]); err != nil {
		return "", time.Time{}, ErrDenied
	}
	key := hex.EncodeToString(id[:])
	raw := "act1." + key + "." + base64.RawURLEncoding.EncodeToString(secret[:])
	group := groupID(installation, resource)
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	l.sweep(now)
	if len(l.tokens) >= maximumTokens {
		return "", time.Time{}, ErrDenied
	}
	if _, collision := l.tokens[key]; collision {
		return "", time.Time{}, ErrDenied
	}
	// At most two live generations per resource can arise through the identity
	// manager's single previous-key overlap. Keep an independent absolute cap.
	if _, exists := l.groups[group]; !exists && len(l.groups) >= 128 {
		return "", time.Time{}, ErrDenied
	}
	until := now.Add(TransportLeaseLifetime)
	installation.PublicKey = append([]byte(nil), installation.PublicKey...)
	installation.Resources = append([]string(nil), installation.Resources...)
	installation.PreviousPublicKey = nil
	incarnation := key
	if current, exists := l.groups[group]; exists {
		incarnation = current.incarnation
	}
	l.groups[group] = permission{installation, resource, now, until, incarnation}
	l.tokens[key] = transportToken{sha256.Sum256([]byte(raw)), group, now, until}
	return raw, until, nil
}

func (l *Leases) sweep(now time.Time) {
	for id, token := range l.tokens {
		if now.Before(token.issued) || !now.Before(token.until) {
			delete(l.tokens, id)
		}
	}
	for id, group := range l.groups {
		if now.Before(group.issued) || !now.Before(group.until) {
			delete(l.groups, id)
		}
	}
}

func (l *Leases) Authenticate(raw string) (Admission, error) {
	if len(raw) != 81 || raw[:5] != "act1." || raw[37] != '.' {
		return Admission{}, ErrDenied
	}
	id, err := hex.DecodeString(raw[5:37])
	secret, secretErr := base64.RawURLEncoding.DecodeString(raw[38:])
	if err != nil || hex.EncodeToString(id) != raw[5:37] || secretErr != nil || len(secret) != 32 || base64.RawURLEncoding.EncodeToString(secret) != raw[38:] {
		return Admission{}, ErrDenied
	}
	l.mu.Lock()
	now := l.now()
	l.sweep(now)
	token, exists := l.tokens[raw[5:37]]
	group, groupExists := l.groups[token.group]
	l.mu.Unlock()
	digest := sha256.Sum256([]byte(raw))
	if !exists || !groupExists || subtle.ConstantTimeCompare(token.digest[:], digest[:]) != 1 || l.authority.Check(group.installation, group.resource) != nil {
		return Admission{}, ErrDenied
	}
	return Admission{group.resource, token.group, group.incarnation}, nil
}

func (l *Leases) Check(admitted Admission) error {
	l.mu.Lock()
	l.sweep(l.now())
	group, exists := l.groups[admitted.group]
	l.mu.Unlock()
	if !exists || group.resource != admitted.Resource || group.incarnation != admitted.incarnation || l.authority.Check(group.installation, group.resource) != nil {
		return ErrDenied
	}
	return nil
}
