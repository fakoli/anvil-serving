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
		fence, err = ensureRouterFence(tx, human)
		return err
	})
	return fence, err
}

// ProvisionRouterIdentity is restricted to the native same-user admin socket.
// It creates no browser session and requires the inspected account generation.
func (m *Manager) ProvisionRouterIdentity(id string, generation uint64) (string, error) {
	if !config.ValidHumanID(id) || generation == 0 {
		return "", ErrDenied
	}
	var fence string
	err := m.state.Update(func(tx *store.Tx) error {
		var human Human
		if err := tx.Get("principals", id, &human); err != nil {
			return ErrDenied
		}
		if human.ID != id || human.Generation != generation || human.Disabled || human.DeletionRequest != "" || !ValidUsername(human.Username) {
			return ErrDenied
		}
		var err error
		fence, err = ensureRouterFence(tx, human)
		return err
	})
	return fence, err
}

func ensureRouterFence(tx *store.Tx, human Human) (string, error) {
	if human.RouterIncarnation == "" {
		var err error
		human.RouterIncarnation, err = randomURLValue(32)
		if err != nil || tx.Put("principals", human.ID, human) != nil {
			return "", ErrUnavailable
		}
	}
	if !validBinding(human.RouterIncarnation) {
		return "", ErrDenied
	}
	return routerFence(tx.Epoch(), human.RouterIncarnation), nil
}

func routerFence(epoch, incarnation string) string {
	digest := sha256.Sum256([]byte(epoch + "\x00" + incarnation))
	return hex.EncodeToString(digest[:])
}

func (m *Manager) CheckRouterPrincipal(id string, generation uint64, fence string) error {
	_, err := m.routerPrincipal(id, generation, fence)
	return err
}

// RouterAccountName is for the operator-only Home view. It returns no identity
// provider subject, session, or stored incarnation, and never labels stale keys
// with the name of an account recreated under the same principal ID.
func (m *Manager) RouterAccountName(id string, generation uint64, fence string) (string, error) {
	human, err := m.routerPrincipal(id, generation, fence)
	return human.Username, err
}

func (m *Manager) routerPrincipal(id string, generation uint64, fence string) (Human, error) {
	if !config.ValidHumanID(id) || generation == 0 || len(fence) != 64 {
		return Human{}, ErrDenied
	}
	var result Human
	err := m.state.View(func(tx *store.Tx) error {
		var human Human
		if err := tx.Get("principals", id, &human); err != nil {
			if errors.Is(err, store.ErrMissing) {
				return ErrDenied
			}
			return ErrUnavailable
		}
		if human.Username != "" && !ValidUsername(human.Username) {
			return ErrUnavailable
		}
		if human.ID != id || human.Disabled || human.DeletionRequest != "" || human.Generation != generation || !validBinding(human.RouterIncarnation) || routerFence(tx.Epoch(), human.RouterIncarnation) != fence {
			return ErrDenied
		}
		result = human
		return nil
	})
	return result, err
}
