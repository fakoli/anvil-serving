package session

import (
	"encoding/json"
	"errors"

	"github.com/fakoli/anvil-serving/connect/internal/store"
)

const (
	MaximumSessions         = 1024
	MaximumSessionsPerHuman = 32
	sessionPrefix           = "session:"
)

func sessionRecord(id string) string { return sessionPrefix + id }

// boundSessions removes authority-invalid records before applying fixed v1
// limits. The database can reuse freed pages; its historical file size need not
// shrink. The absolute snapshot bound does not depend on an operator setting.
func (m *Manager) boundSessions(tx *store.Tx, principal string) error {
	records, err := tx.List("sessions", sessionPrefix, MaximumSessions)
	if err != nil {
		return ErrUnavailable
	}
	active, forHuman := 0, 0
	for _, item := range records {
		var session Session
		if json.Unmarshal(item.Value, &session) != nil || item.ID != sessionRecord(session.ID) {
			return ErrUnavailable
		}
		var human Human
		err := tx.Get("principals", session.Principal, &human)
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return ErrUnavailable
		}
		rule, configured := m.rules[session.Resource]
		stale := session.Revoked || session.Generation == 0 || session.Epoch != tx.Epoch() || !tx.Now().Before(session.ExpiresAt) || tx.Now().Before(session.IssuedAt) || errors.Is(err, store.ErrMissing) || human.ID != session.Principal || human.Disabled || human.Generation != session.PrincipalGeneration || !hasResource(human.Resources, session.Resource) || !configured || rule.Host != session.Host
		if stale {
			if tx.Delete("sessions", item.ID) != nil {
				return ErrUnavailable
			}
			continue
		}
		active++
		if session.Principal == principal {
			forHuman++
		}
	}
	if active >= MaximumSessions || forHuman >= MaximumSessionsPerHuman {
		return ErrDenied
	}
	return nil
}
