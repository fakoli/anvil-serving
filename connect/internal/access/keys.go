package access

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"math"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

var (
	ErrDenied      = errors.New("access denied")
	ErrGrant       = errors.New("invalid or excessive grant")
	ErrUnavailable = errors.New("authority unavailable")
)

const DefaultKeyLifetime = 24 * time.Hour
const MaximumKeyLifetime = 30 * 24 * time.Hour

type Grant struct {
	Resource string   `json:"resource"`
	Methods  []string `json:"methods"`
}

type Principal struct {
	ID         string  `json:"id"`
	Generation uint64  `json:"generation"`
	Disabled   bool    `json:"disabled"`
	Grants     []Grant `json:"grants"`
}

// Key contains no recoverable bearer credential. Digest is a SHA-256 hash of a
// 256-bit random secret and random public identifier, not a password hash.
type Key struct {
	ID                      string    `json:"id"`
	Digest                  [32]byte  `json:"digest"`
	Principal               string    `json:"principal"`
	PrincipalGeneration     uint64    `json:"principal_generation"`
	Generation              uint64    `json:"generation"`
	Epoch                   string    `json:"epoch"`
	IssuedAt                time.Time `json:"issued_at"`
	ExpiresAt               time.Time `json:"expires_at"`
	Revoked                 bool      `json:"revoked"`
	Grants                  []Grant   `json:"grants"`
	DeviceHuman             string    `json:"device_human,omitempty"`
	DeviceHumanGeneration   uint64    `json:"device_human_generation,omitempty"`
	DeviceMappingHash       string    `json:"device_mapping_hash,omitempty"`
	DeviceSession           string    `json:"device_session,omitempty"`
	DeviceSessionGeneration uint64    `json:"device_session_generation,omitempty"`
}

// Admission is a short-lived authorization snapshot. Active transports must
// recheck it for revocation; accepting it once is not an enduring capability.
type Admission struct {
	KeyID                   string
	KeyGeneration           uint64
	Principal               string
	PrincipalGeneration     uint64
	Epoch                   string
	ExpiresAt               time.Time
	Resource                string
	Method                  string
	DeviceHuman             string
	DeviceHumanGeneration   uint64
	DeviceMappingHash       string
	DeviceSession           string
	DeviceSessionGeneration uint64
}

// DeviceCredential ties a short-lived API key to an independently admitted
// human session and one immutable declared mapping. It never contains the raw
// bearer credential or an IdP subject.
type DeviceCredential struct {
	HumanID           string
	HumanGeneration   uint64
	MappingHash       string
	Session           string
	SessionGeneration uint64
}

// DeviceChecker is installed only by the gateway device authority. It is
// called outside the key-store transaction so it may re-read human authority
// state without nesting Bolt transactions.
type DeviceChecker func(DeviceCredential, string, string, string, uint64) error

type Keys struct {
	state         *store.Store
	rules         map[string]config.Rule
	checkerMu     sync.RWMutex
	deviceChecker DeviceChecker
}

func NewKeys(state *store.Store, rules []config.Rule) (*Keys, error) {
	if state == nil || len(rules) == 0 || len(rules) > 64 {
		return nil, ErrGrant
	}
	k := &Keys{state: state, rules: map[string]config.Rule{}}
	for _, rule := range rules {
		if rule.Validate() != nil || rule.Access != "api" {
			return nil, ErrGrant
		}
		if _, exists := k.rules[rule.ID]; exists {
			return nil, ErrGrant
		}
		rule.Methods = append([]string(nil), rule.Methods...)
		k.rules[rule.ID] = rule
	}
	return k, nil
}

func (k *Keys) grants(grants []Grant) ([]Grant, error) {
	if len(grants) < 1 || len(grants) > len(k.rules) {
		return nil, ErrGrant
	}
	seen := map[string]bool{}
	result := make([]Grant, 0, len(grants))
	for _, grant := range grants {
		rule, exists := k.rules[grant.Resource]
		if !exists || seen[grant.Resource] || len(grant.Methods) < 1 || len(grant.Methods) > len(rule.Methods) {
			return nil, ErrGrant
		}
		seen[grant.Resource] = true
		methods := map[string]bool{}
		copy := Grant{Resource: grant.Resource, Methods: append([]string(nil), grant.Methods...)}
		for _, method := range copy.Methods {
			if methods[method] || !rule.Allows(rule.Host, rule.PathPrefix, method) {
				return nil, ErrGrant
			}
			methods[method] = true
		}
		sort.Strings(copy.Methods)
		result = append(result, copy)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Resource < result[j].Resource })
	return result, nil
}

func permits(grants []Grant, resource, method string) bool {
	for _, grant := range grants {
		if grant.Resource == resource {
			for _, allowed := range grant.Methods {
				if method == allowed {
					return true
				}
			}
		}
	}
	return false
}

// SetPrincipal is an administrative operation. Every change invalidates all
// existing keys, including disable/re-enable; generations are never reused.
func (k *Keys) SetPrincipal(id string, grants []Grant, disabled bool) error {
	if !config.ValidID(id) {
		return ErrGrant
	}
	grants, err := k.grants(grants)
	if err != nil {
		return err
	}
	return k.state.Update(func(tx *store.Tx) error {
		var old Principal
		err := tx.Get("principals", id, &old)
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return ErrUnavailable
		}
		if old.Generation == math.MaxUint64 {
			return ErrUnavailable
		}
		return tx.Put("principals", id, Principal{ID: id, Generation: old.Generation + 1, Disabled: disabled, Grants: grants})
	})
}

// SetDeviceChecker installs the gateway's human/mapping recheck before any
// device-derived credential is accepted. Ordinary API keys never invoke it.
// It is setup-time only and replaces no configured checker at runtime.
func (k *Keys) SetDeviceChecker(checker DeviceChecker) error {
	if k == nil || checker == nil {
		return ErrGrant
	}
	k.checkerMu.Lock()
	defer k.checkerMu.Unlock()
	if k.deviceChecker != nil {
		return ErrGrant
	}
	k.deviceChecker = checker
	return nil
}

// Issue returns the raw credential only to its administrative caller. It must
// be delivered once through a secret channel, never printed by request logs.
func (k *Keys) Issue(principal string, grants []Grant, lifetime time.Duration) (string, Key, error) {
	return k.issue(principal, grants, lifetime, DeviceCredential{})
}

// IssueDevice creates a normal API bearer only after the caller has already
// checked one configured human-to-principal mapping. Every later admission
// rechecks that mapping and human generation through DeviceChecker.
func (k *Keys) IssueDevice(principal string, grants []Grant, lifetime time.Duration, device DeviceCredential) (string, Key, error) {
	if !config.ValidHumanID(device.HumanID) || device.HumanGeneration == 0 || !lowerHex(device.MappingHash, 32) || !lowerHex(device.Session, 16) || device.SessionGeneration == 0 {
		return "", Key{}, ErrGrant
	}
	return k.issue(principal, grants, lifetime, device)
}

func lowerHex(value string, bytes int) bool {
	if len(value) != bytes*2 {
		return false
	}
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == bytes && hex.EncodeToString(decoded) == value
}

func (k *Keys) issue(principal string, grants []Grant, lifetime time.Duration, device DeviceCredential) (string, Key, error) {
	if lifetime == 0 {
		lifetime = DefaultKeyLifetime
	}
	if lifetime < time.Minute || lifetime > MaximumKeyLifetime || !config.ValidID(principal) {
		return "", Key{}, ErrGrant
	}
	grants, err := k.grants(grants)
	if err != nil {
		return "", Key{}, err
	}
	var random [48]byte
	if _, err := rand.Read(random[:]); err != nil {
		return "", Key{}, ErrUnavailable
	}
	id := hex.EncodeToString(random[:16])
	prefix := "ac1"
	if device.HumanID != "" {
		prefix = "acd1"
	}
	raw := prefix + "." + id + "." + base64.RawURLEncoding.EncodeToString(random[16:])
	key := Key{ID: id, Digest: sha256.Sum256([]byte(raw)), Principal: principal, Generation: 1, Grants: grants, DeviceHuman: device.HumanID, DeviceHumanGeneration: device.HumanGeneration, DeviceMappingHash: device.MappingHash, DeviceSession: device.Session, DeviceSessionGeneration: device.SessionGeneration}
	err = k.state.Update(func(tx *store.Tx) error {
		var owner Principal
		if tx.Get("principals", principal, &owner) != nil || owner.Disabled || owner.Generation == 0 {
			return ErrDenied
		}
		for _, grant := range grants {
			for _, method := range grant.Methods {
				if !permits(owner.Grants, grant.Resource, method) {
					return ErrGrant
				}
			}
		}
		var collision Key
		if err := tx.Get("api_keys", id, &collision); !errors.Is(err, store.ErrMissing) {
			return ErrUnavailable
		}
		key.PrincipalGeneration = owner.Generation
		key.Epoch = tx.Epoch()
		key.IssuedAt = tx.Now()
		key.ExpiresAt = key.IssuedAt.Add(lifetime)
		return tx.Put("api_keys", id, key)
	})
	if err != nil {
		return "", Key{}, err
	}
	return raw, key, nil
}

func tokenID(raw string) (string, bool) {
	id, device, ok := parseTokenID(raw)
	return id, ok && !device
}

// parseTokenID accepts the current ordinary and device credential grammars.
// acd1 is deliberately outside the older ac1 grammar, so a rollback cannot
// admit a device-derived bearer without its human/mapping recheck.
func parseTokenID(raw string) (string, bool, bool) {
	parts := strings.Split(raw, ".")
	if len(parts) != 3 || (parts[0] != "ac1" && parts[0] != "acd1") || len(parts[1]) != 32 || len(parts[2]) != 43 {
		return "", false, false
	}
	expectedLength := 80
	if parts[0] == "acd1" {
		expectedLength = 81
	}
	if len(raw) != expectedLength {
		return "", false, false
	}
	id, err := hex.DecodeString(parts[1])
	if err != nil || hex.EncodeToString(id) != parts[1] {
		return "", false, false
	}
	secret, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil || len(secret) != 32 || base64.RawURLEncoding.EncodeToString(secret) != parts[2] {
		return "", false, false
	}
	return parts[1], parts[0] == "acd1", true
}

func (k *Keys) authorize(tx *store.Tx, id, resource, method string) (Key, error) {
	var key Key
	var principal Principal
	rule, exists := k.rules[resource]
	if !exists || !rule.Allows(rule.Host, rule.PathPrefix, method) || tx.Get("api_keys", id, &key) != nil || key.Revoked || key.Generation == 0 || key.ID != id || key.Epoch != tx.Epoch() || !tx.Now().Before(key.ExpiresAt) || tx.Now().Before(key.IssuedAt) || !permits(key.Grants, resource, method) {
		return Key{}, ErrDenied
	}
	if tx.Get("principals", key.Principal, &principal) != nil || principal.Disabled || principal.Generation != key.PrincipalGeneration || principal.ID != key.Principal || !permits(principal.Grants, resource, method) {
		return Key{}, ErrDenied
	}
	return key, nil
}

func (k *Keys) Authenticate(raw, resource, method string) (Admission, error) {
	id, deviceToken, ok := parseTokenID(raw)
	if !ok {
		return Admission{}, ErrDenied
	}
	digest := sha256.Sum256([]byte(raw))
	var admitted Admission
	err := k.state.View(func(tx *store.Tx) error {
		key, err := k.authorize(tx, id, resource, method)
		deviceKey := key.DeviceHuman != "" || key.DeviceHumanGeneration != 0 || key.DeviceMappingHash != ""
		if err != nil || deviceToken != deviceKey || subtle.ConstantTimeCompare(key.Digest[:], digest[:]) != 1 {
			return ErrDenied
		}
		admitted = Admission{KeyID: key.ID, KeyGeneration: key.Generation, Principal: key.Principal, PrincipalGeneration: key.PrincipalGeneration, Epoch: key.Epoch, ExpiresAt: key.ExpiresAt, Resource: resource, Method: method, DeviceHuman: key.DeviceHuman, DeviceHumanGeneration: key.DeviceHumanGeneration, DeviceMappingHash: key.DeviceMappingHash, DeviceSession: key.DeviceSession, DeviceSessionGeneration: key.DeviceSessionGeneration}
		return nil
	})
	if err != nil {
		return Admission{}, ErrDenied
	}
	if err := k.checkDevice(DeviceCredential{HumanID: admitted.DeviceHuman, HumanGeneration: admitted.DeviceHumanGeneration, MappingHash: admitted.DeviceMappingHash, Session: admitted.DeviceSession, SessionGeneration: admitted.DeviceSessionGeneration}, admitted.Principal, admitted.Resource, admitted.Method, admitted.PrincipalGeneration); err != nil {
		return Admission{}, ErrDenied
	}
	return admitted, nil
}

func (k *Keys) Check(admitted Admission) error {
	err := k.state.View(func(tx *store.Tx) error {
		key, err := k.authorize(tx, admitted.KeyID, admitted.Resource, admitted.Method)
		if err != nil || key.Generation != admitted.KeyGeneration || key.Principal != admitted.Principal || key.PrincipalGeneration != admitted.PrincipalGeneration || key.Epoch != admitted.Epoch || !key.ExpiresAt.Equal(admitted.ExpiresAt) || key.DeviceHuman != admitted.DeviceHuman || key.DeviceHumanGeneration != admitted.DeviceHumanGeneration || key.DeviceMappingHash != admitted.DeviceMappingHash || key.DeviceSession != admitted.DeviceSession || key.DeviceSessionGeneration != admitted.DeviceSessionGeneration {
			return ErrDenied
		}
		return nil
	})
	if err != nil {
		return err
	}
	return k.checkDevice(DeviceCredential{HumanID: admitted.DeviceHuman, HumanGeneration: admitted.DeviceHumanGeneration, MappingHash: admitted.DeviceMappingHash, Session: admitted.DeviceSession, SessionGeneration: admitted.DeviceSessionGeneration}, admitted.Principal, admitted.Resource, admitted.Method, admitted.PrincipalGeneration)
}

func (k *Keys) checkDevice(device DeviceCredential, principal, resource, method string, principalGeneration uint64) error {
	if device.HumanID == "" && device.HumanGeneration == 0 && device.MappingHash == "" && device.Session == "" && device.SessionGeneration == 0 {
		return nil
	}
	if !config.ValidHumanID(device.HumanID) || device.HumanGeneration == 0 || !lowerHex(device.MappingHash, 32) || !lowerHex(device.Session, 16) || device.SessionGeneration == 0 {
		return ErrDenied
	}
	k.checkerMu.RLock()
	checker := k.deviceChecker
	k.checkerMu.RUnlock()
	if checker == nil || checker(device, principal, resource, method, principalGeneration) != nil {
		return ErrDenied
	}
	return nil
}

func (k *Keys) Revoke(id string) error {
	return k.state.Update(func(tx *store.Tx) error {
		var key Key
		if tx.Get("api_keys", id, &key) != nil {
			return ErrDenied
		}
		if key.Revoked {
			return nil
		}
		if key.Generation == math.MaxUint64 {
			return ErrUnavailable
		}
		key.Revoked = true
		key.Generation++
		return tx.Put("api_keys", id, key)
	})
}
