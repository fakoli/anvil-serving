// Package identity registers public installation keys and verifies bounded
// possession proofs. It never distributes a shared installation private key.
package identity

import (
	"crypto"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	jose "github.com/go-jose/go-jose/v4"
)

var ErrDenied = errors.New("installation proof or authority denied")

type Invitation struct {
	ID           string    `json:"id"`
	Digest       [32]byte  `json:"digest"`
	Installation string    `json:"installation"`
	Role         string    `json:"role"`
	Resources    []string  `json:"resources"`
	Epoch        string    `json:"epoch"`
	ExpiresAt    time.Time `json:"expires_at"`
	Consumed     bool      `json:"consumed"`
	Generation   uint64    `json:"generation"`
}

type Installation struct {
	ID                  string          `json:"id"`
	Role                string          `json:"role"`
	Resources           []string        `json:"resources"`
	PublicKey           json.RawMessage `json:"public_key"`
	Fingerprint         string          `json:"fingerprint"`
	Generation          uint64          `json:"generation"`
	Epoch               string          `json:"epoch"`
	Status              string          `json:"status"`
	RequestedAt         time.Time       `json:"requested_at"`
	ApprovalExpiresAt   time.Time       `json:"approval_expires_at"`
	PreviousPublicKey   json.RawMessage `json:"previous_public_key,omitempty"`
	PreviousFingerprint string          `json:"previous_fingerprint,omitempty"`
	PreviousGeneration  uint64          `json:"previous_generation,omitempty"`
	PreviousUntil       time.Time       `json:"previous_until,omitempty"`
}

type Manager struct {
	state     *store.Store
	audience  string
	resources map[string]bool
}

func NewManager(state *store.Store, audience string, resources []string) (*Manager, error) {
	u, err := url.Parse(audience)
	if state == nil || err != nil || u.Scheme != "https" || !config.ValidHost(u.Host) || u.User != nil || u.Path != "" || u.RawQuery != "" || strings.ContainsAny(audience, "?#") || len(resources) == 0 || len(resources) > 64 {
		return nil, ErrDenied
	}
	m := &Manager{state: state, audience: audience, resources: map[string]bool{}}
	for _, id := range resources {
		if !config.ValidID(id) || m.resources[id] {
			return nil, ErrDenied
		}
		m.resources[id] = true
	}
	return m, nil
}

func validRole(role string) bool { return role == "connector" || role == "client" }

func randomID() (string, error) {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", ErrDenied
	}
	return hex.EncodeToString(value[:]), nil
}

func (m *Manager) assigned(resources []string) ([]string, error) {
	if len(resources) < 1 || len(resources) > len(m.resources) {
		return nil, ErrDenied
	}
	result := append([]string(nil), resources...)
	sort.Strings(result)
	for i, id := range result {
		if !m.resources[id] || (i > 0 && id == result[i-1]) {
			return nil, ErrDenied
		}
	}
	return result, nil
}

// Invite is an administrative operation with a ten-minute maximum lifetime.
// The returned bearer invitation is shown once; only its digest is persisted.
func (m *Manager) Invite(installation, role string, resources []string, lifetime time.Duration) (string, Invitation, error) {
	if !config.ValidID(installation) || !validRole(role) || lifetime < time.Minute || lifetime > 10*time.Minute {
		return "", Invitation{}, ErrDenied
	}
	resources, err := m.assigned(resources)
	if err != nil {
		return "", Invitation{}, err
	}
	id, err := randomID()
	if err != nil {
		return "", Invitation{}, err
	}
	var secret [32]byte
	if _, err := rand.Read(secret[:]); err != nil {
		return "", Invitation{}, ErrDenied
	}
	raw := "aci1." + id + "." + base64.RawURLEncoding.EncodeToString(secret[:])
	invitation := Invitation{ID: id, Digest: sha256.Sum256([]byte(raw)), Installation: installation, Role: role, Resources: resources}
	err = m.state.Update(func(tx *store.Tx) error {
		var existing Installation
		if err := tx.Get("installations", installation, &existing); err != nil {
			if !errors.Is(err, store.ErrMissing) {
				return ErrDenied
			}
		} else {
			staleEpoch := existing.Epoch != tx.Epoch()
			expiredPending := (existing.Status == "invited" || existing.Status == "pending") && !tx.Now().Before(existing.ApprovalExpiresAt)
			if !staleEpoch && !expiredPending && existing.Status != "revoked" {
				return ErrDenied
			}
		}
		if existing.Generation == math.MaxUint64 {
			return ErrDenied
		}
		var collision Invitation
		if err := tx.Get("invitations", id, &collision); !errors.Is(err, store.ErrMissing) {
			return ErrDenied
		}
		invitation.Epoch = tx.Epoch()
		invitation.ExpiresAt = tx.Now().Add(lifetime)
		invitation.Generation = existing.Generation + 1
		if err := tx.Put("invitations", id, invitation); err != nil {
			return err
		}
		// Reserve the next generation atomically. Replacement is local admin
		// authority only, and earlier invitations cannot consume this generation.
		return tx.Put("installations", installation, Installation{ID: installation, Role: role, Resources: resources, Generation: invitation.Generation, Epoch: tx.Epoch(), Status: "invited", RequestedAt: tx.Now(), ApprovalExpiresAt: invitation.ExpiresAt})
	})
	if err != nil {
		return "", Invitation{}, ErrDenied
	}
	return raw, invitation, nil
}

func invitationID(raw string) (string, bool) {
	if len(raw) != 81 {
		return "", false
	}
	parts := strings.Split(raw, ".")
	if len(parts) != 3 || parts[0] != "aci1" || !validNonce(parts[1]) {
		return "", false
	}
	secret, err := base64.RawURLEncoding.DecodeString(parts[2])
	return parts[1], err == nil && len(secret) == 32 && base64.RawURLEncoding.EncodeToString(secret) == parts[2]
}

func PublicKey(private ed25519.PrivateKey) (json.RawMessage, string, error) {
	if len(private) != ed25519.PrivateKeySize {
		return nil, "", ErrDenied
	}
	jwk := jose.JSONWebKey{Key: private.Public(), Algorithm: string(jose.EdDSA), Use: "sig"}
	encoded, err := json.Marshal(jwk)
	if err != nil {
		return nil, "", ErrDenied
	}
	_, fingerprint, err := parsePublic(encoded)
	return encoded, fingerprint, err
}

func parsePublic(encoded []byte) (ed25519.PublicKey, string, error) {
	if len(encoded) > 4096 {
		return nil, "", ErrDenied
	}
	var jwk jose.JSONWebKey
	if json.Unmarshal(encoded, &jwk) != nil || !jwk.Valid() || !jwk.IsPublic() || (jwk.Algorithm != "" && jwk.Algorithm != string(jose.EdDSA)) || (jwk.Use != "" && jwk.Use != "sig") {
		return nil, "", ErrDenied
	}
	key, ok := jwk.Key.(ed25519.PublicKey)
	if !ok || len(key) != ed25519.PublicKeySize {
		return nil, "", ErrDenied
	}
	thumbprint, err := jwk.Thumbprint(crypto.SHA256)
	if err != nil {
		return nil, "", ErrDenied
	}
	return key, base64.RawURLEncoding.EncodeToString(thumbprint), nil
}

// EnrollmentNonce binds the possession proof to its invitation without
// embedding the bearer invitation in a JWT that might be inspected separately.
func EnrollmentNonce(rawInvitation string) string {
	digest := sha256.Sum256([]byte(rawInvitation))
	return hex.EncodeToString(digest[:])
}

func (m *Manager) Enroll(rawInvitation string, public json.RawMessage, proof string) (Installation, error) {
	id, ok := invitationID(rawInvitation)
	if !ok {
		return Installation{}, ErrDenied
	}
	key, fingerprint, err := parsePublic(public)
	if err != nil {
		return Installation{}, err
	}
	canonical, err := json.Marshal(jose.JSONWebKey{Key: key, Algorithm: string(jose.EdDSA), Use: "sig"})
	if err != nil {
		return Installation{}, ErrDenied
	}
	digest := sha256.Sum256([]byte(rawInvitation))
	var result Installation
	err = m.state.Update(func(tx *store.Tx) error {
		var invitation Invitation
		if tx.Get("invitations", id, &invitation) != nil || invitation.Consumed || invitation.Epoch != tx.Epoch() || !tx.Now().Before(invitation.ExpiresAt) || subtle.ConstantTimeCompare(digest[:], invitation.Digest[:]) != 1 {
			return ErrDenied
		}
		claims, err := verify(proof, key, invitation.Installation, m.audience+"/enroll", tx.Now())
		if err != nil || claims.Role != invitation.Role || claims.Resource != "" || claims.Epoch != tx.Epoch() || claims.Generation != 0 || claims.Nonce != EnrollmentNonce(rawInvitation) {
			return ErrDenied
		}
		if _, err := m.assigned(invitation.Resources); err != nil {
			return ErrDenied
		}
		var existing Installation
		if tx.Get("installations", invitation.Installation, &existing) != nil || existing.Status != "invited" || existing.Epoch != tx.Epoch() || existing.Generation != invitation.Generation {
			return ErrDenied
		}
		result = Installation{ID: invitation.Installation, Role: invitation.Role, Resources: invitation.Resources, PublicKey: canonical, Fingerprint: fingerprint, Generation: invitation.Generation, Epoch: tx.Epoch(), Status: "pending", RequestedAt: tx.Now(), ApprovalExpiresAt: tx.Now().Add(10 * time.Minute)}
		invitation.Consumed = true
		if err := tx.Put("invitations", id, invitation); err != nil {
			return err
		}
		return tx.Put("installations", result.ID, result)
	})
	if err != nil {
		return Installation{}, ErrDenied
	}
	return result, nil
}

// Approve must be reached only through local administrative authority and an
// independently checked fingerprint, never by an enrollee's HTTP request.
func (m *Manager) Approve(id, fingerprint string) error {
	return m.state.Update(func(tx *store.Tx) error {
		var installation Installation
		if tx.Get("installations", id, &installation) != nil || installation.Status != "pending" || installation.Epoch != tx.Epoch() || !tx.Now().Before(installation.ApprovalExpiresAt) || subtle.ConstantTimeCompare([]byte(fingerprint), []byte(installation.Fingerprint)) != 1 {
			return ErrDenied
		}
		installation.Status = "active"
		return tx.Put("installations", id, installation)
	})
}
