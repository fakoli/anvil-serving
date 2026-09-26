package session

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

// RouterFence survives sign-out but never account deletion/recreation or recovery.
// Existing accounts acquire a random incarnation only on authenticated first use.
func (m *Manager) RouterFence(raw, host string) (string, error) {
	var fence string
	err := m.state.Update(func(tx *store.Tx) error {
		_, human, err := m.authorize(tx, raw, host, false)
		if err != nil || human.DeletionRequest != "" {
			return ErrDenied
		}
		if human.RouterIncarnation == "" {
			human.RouterIncarnation, err = randomURLValue(32)
			if err != nil {
				return ErrUnavailable
			}
			if tx.Put("principals", human.ID, human) != nil {
				return ErrUnavailable
			}
		}
		if !validBinding(human.RouterIncarnation) {
			return ErrDenied
		}
		fence = routerFence(tx.Epoch(), human.RouterIncarnation)
		return nil
	})
	return fence, err
}

func routerFence(epoch, incarnation string) string {
	digest := sha256.Sum256([]byte(epoch + "\x00" + incarnation))
	return hex.EncodeToString(digest[:])
}

func (m *Manager) CheckRouterPrincipal(id string, generation uint64, fence string) error {
	if !config.ValidHumanID(id) || generation == 0 || len(fence) != 64 {
		return ErrDenied
	}
	return m.state.View(func(tx *store.Tx) error {
		var human Human
		if err := tx.Get("principals", id, &human); err != nil {
			if errors.Is(err, store.ErrMissing) {
				return ErrDenied
			}
			return ErrUnavailable
		}
		if human.ID != id || human.Disabled || human.DeletionRequest != "" || human.Generation != generation || !validBinding(human.RouterIncarnation) || routerFence(tx.Epoch(), human.RouterIncarnation) != fence {
			return ErrDenied
		}
		return nil
	})
}
