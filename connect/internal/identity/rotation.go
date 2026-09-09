package identity

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"math"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	jose "github.com/go-jose/go-jose/v4"
)

const (
	DefaultRotationOverlap = time.Minute
	MaximumRotationOverlap = 5 * time.Minute
	maxReplayMarkers       = 512
)

func activeForResource(installation Installation, tx *store.Tx, resource string) bool {
	if installation.Status != "active" || installation.Epoch != tx.Epoch() || !validRole(installation.Role) || !config.ValidID(resource) {
		return false
	}
	for _, allowed := range installation.Resources {
		if allowed == resource {
			return true
		}
	}
	return false
}

func samePublicKey(left, right json.RawMessage) bool {
	return subtle.ConstantTimeCompare(left, right) == 1
}

// Check revalidates a previously verified installation snapshot against local
// authority state. During a bounded rotation overlap it recognizes only the
// immediately preceding key/generation contained in that snapshot.
func (m *Manager) Check(snapshot Installation, resource string) error {
	_, err := m.PermissionLifetime(snapshot, resource, time.Hour)
	return err
}

// PermissionLifetime bounds a freshly authenticated control response. In the
// prior-key overlap it never extends permission beyond PreviousUntil. The
// connector anchors this duration to its local request-start time, so network
// delay shortens permission rather than extending the rotation window.
func (m *Manager) PermissionLifetime(snapshot Installation, resource string, maximum time.Duration) (time.Duration, error) {
	if !config.ValidID(snapshot.ID) || !m.resources[resource] {
		return 0, ErrDenied
	}
	if maximum <= 0 || maximum > time.Hour {
		return 0, ErrDenied
	}
	var lifetime time.Duration
	err := m.state.View(func(tx *store.Tx) error {
		var current Installation
		if tx.Get("installations", snapshot.ID, &current) != nil || !activeForResource(current, tx, resource) || current.Role != snapshot.Role {
			return ErrDenied
		}
		_, fingerprint, err := parsePublic(current.PublicKey)
		if err != nil || fingerprint != current.Fingerprint {
			return ErrDenied
		}
		if snapshot.Epoch != tx.Epoch() {
			return ErrDenied
		}
		if snapshot.Generation == current.Generation && snapshot.Fingerprint == current.Fingerprint && samePublicKey(snapshot.PublicKey, current.PublicKey) {
			lifetime = maximum
			return nil
		}
		if !current.PreviousUntil.IsZero() && tx.Now().Before(current.PreviousUntil) && snapshot.Generation == current.PreviousGeneration && snapshot.Fingerprint == current.PreviousFingerprint && samePublicKey(snapshot.PublicKey, current.PreviousPublicKey) {
			_, fingerprint, err := parsePublic(current.PreviousPublicKey)
			if err != nil || fingerprint != current.PreviousFingerprint {
				return ErrDenied
			}
			lifetime = min(maximum, current.PreviousUntil.Sub(tx.Now()))
			return nil
		}
		return ErrDenied
	})
	return lifetime, err
}

// Revoke is idempotent after its first generation advance. A revoked record
// retains only enough public identity for an owner-driven re-enrollment invite.
func (m *Manager) Revoke(id string) error {
	if !config.ValidID(id) {
		return ErrDenied
	}
	return m.state.Update(func(tx *store.Tx) error {
		var installation Installation
		if tx.Get("installations", id, &installation) != nil {
			return ErrDenied
		}
		if installation.Status == "revoked" {
			return nil
		}
		if installation.Generation == math.MaxUint64 {
			return ErrDenied
		}
		installation.Generation++
		installation.Epoch = tx.Epoch()
		installation.Status = "revoked"
		installation.PreviousPublicKey = nil
		installation.PreviousFingerprint = ""
		installation.PreviousGeneration = 0
		installation.PreviousUntil = time.Time{}
		return tx.Put("installations", id, installation)
	})
}

// Rotate requires possession of the installed current key and a proposed new
// key. The server challenge binds both compact JWT proofs; callers cannot use a
// caller-selected nonce to rotate an unrelated installation.
func (m *Manager) Rotate(id string, newPublic json.RawMessage, oldProof, newProof, expectedChallenge string, overlap time.Duration) (Installation, error) {
	if !config.ValidID(id) || !validNonce(expectedChallenge) || overlap < 0 || overlap > MaximumRotationOverlap || oldProof == "" || newProof == "" {
		return Installation{}, ErrDenied
	}
	newKey, newFingerprint, err := parsePublic(newPublic)
	if err != nil {
		return Installation{}, ErrDenied
	}
	canonical, err := json.Marshal(jose.JSONWebKey{Key: newKey, Algorithm: string(jose.EdDSA), Use: "sig"})
	if err != nil {
		return Installation{}, ErrDenied
	}
	var result Installation
	err = m.state.Update(func(tx *store.Tx) error {
		var installation Installation
		if tx.Get("installations", id, &installation) != nil || installation.Status != "active" || installation.Epoch != tx.Epoch() || installation.Generation == math.MaxUint64 || (!installation.PreviousUntil.IsZero() && tx.Now().Before(installation.PreviousUntil)) {
			return ErrDenied
		}
		oldKey, oldFingerprint, err := parsePublic(installation.PublicKey)
		if err != nil || oldFingerprint != installation.Fingerprint || newFingerprint == oldFingerprint {
			return ErrDenied
		}
		oldClaims, err := verify(oldProof, oldKey, id, m.audience+"/rotate", tx.Now())
		if err != nil {
			return ErrDenied
		}
		newClaims, err := verify(newProof, newKey, id, m.audience+"/rotate", tx.Now())
		if err != nil || oldClaims.ID == newClaims.ID || oldClaims.Nonce != expectedChallenge || newClaims.Nonce != expectedChallenge || oldClaims.Fingerprint != newFingerprint || newClaims.Fingerprint != newFingerprint || oldClaims.Role != installation.Role || newClaims.Role != installation.Role || oldClaims.Resource != "" || newClaims.Resource != "" || oldClaims.Epoch != tx.Epoch() || newClaims.Epoch != tx.Epoch() || oldClaims.Generation != installation.Generation || newClaims.Generation != installation.Generation {
			return ErrDenied
		}
		if err := m.consumeReplay(tx, id, map[string]time.Time{
			"rotation:old-jti:" + oldClaims.ID: oldClaims.Expiry.Time(),
			"rotation:new-jti:" + newClaims.ID: newClaims.Expiry.Time(),
			"nonce:" + expectedChallenge:       minTime(oldClaims.Expiry.Time(), newClaims.Expiry.Time()),
		}); err != nil {
			return ErrDenied
		}
		installation.PreviousPublicKey = append(json.RawMessage(nil), installation.PublicKey...)
		installation.PreviousFingerprint = installation.Fingerprint
		installation.PreviousGeneration = installation.Generation
		installation.PreviousUntil = tx.Now().Add(overlap)
		installation.PublicKey = canonical
		installation.Fingerprint = newFingerprint
		installation.Generation++
		result = installation
		return tx.Put("installations", id, installation)
	})
	if err != nil {
		return Installation{}, ErrDenied
	}
	return result, nil
}

func minTime(left, right time.Time) time.Time {
	if left.Before(right) {
		return left
	}
	return right
}

func (m *Manager) consumeReplay(tx *store.Tx, id string, markers map[string]time.Time) error {
	var window replayWindow
	if err := tx.Get("replay", id, &window); err != nil && !errors.Is(err, store.ErrMissing) {
		return ErrDenied
	}
	if window.Entries == nil {
		window.Entries = map[string]time.Time{}
	}
	for marker, expiry := range window.Entries {
		if !tx.Now().Before(expiry) {
			delete(window.Entries, marker)
		}
	}
	if len(markers) == 0 || len(window.Entries)+len(markers) > maxReplayMarkers {
		return ErrDenied
	}
	for marker, expiry := range markers {
		if marker == "" || !tx.Now().Before(expiry) || window.Entries[marker] != (time.Time{}) {
			return ErrDenied
		}
	}
	for marker, expiry := range markers {
		window.Entries[marker] = expiry
	}
	return tx.Put("replay", id, window)
}
