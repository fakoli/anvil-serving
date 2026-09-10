package session

import (
	"errors"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"math"
)

// ConfigureAdministration installs an immutable startup policy. It is called
// before the manager is exposed to either HTTP or local authority commands.
func (m *Manager) ConfigureAdministration(settings config.BrowserAdministration) error {
	if m.administration != nil || len(settings.Operators) < 1 || len(settings.Operators) > 64 {
		return ErrConfiguration
	}
	rule, ok := m.rules[settings.BrowserResource]
	if !ok || !rule.Allows(rule.Host, rule.PathPrefix, "GET") || !rule.Allows(rule.Host, rule.PathPrefix, "POST") {
		return ErrConfiguration
	}
	seen := map[string]bool{}
	for _, id := range settings.Operators {
		if !config.ValidHumanID(id) || seen[id] {
			return ErrConfiguration
		}
		seen[id] = true
	}
	copied := settings
	copied.Operators = append([]string(nil), settings.Operators...)
	m.administration = &copied
	return nil
}

func (m *Manager) preserveAdministrators(tx *store.Tx, proposed Human) error {
	if m.administration == nil {
		return nil
	}
	for _, id := range m.administration.Operators {
		var human Human
		if id == proposed.ID {
			human = proposed
		} else {
			err := tx.Get("principals", id, &human)
			if errors.Is(err, store.ErrMissing) {
				continue
			}
			if err != nil || human.ID != id || human.Generation == 0 {
				return ErrUnavailable
			}
		}
		if !human.Disabled && hasResource(human.Resources, m.administration.BrowserResource) {
			return nil
		}
	}
	return ErrConflict
}

// UpdateHumanTx updates one existing human in the caller's transaction. Both
// web administration and local SetHuman retain the last configured operator.
func (m *Manager) UpdateHumanTx(tx *store.Tx, id string, expected uint64, resources []string, disabled bool) error {
	if tx == nil || !config.ValidHumanID(id) || expected == 0 {
		return ErrDenied
	}
	resources, ok := m.validateResources(resources)
	if !ok {
		return ErrDenied
	}
	var human Human
	if tx.Get("principals", id, &human) != nil || human.ID != id || human.Generation == 0 {
		return ErrDenied
	}
	if human.Generation != expected {
		return ErrConflict
	}
	if human.Generation == math.MaxUint64 {
		return ErrUnavailable
	}
	human.Generation++
	human.Resources = resources
	human.Disabled = disabled
	if err := m.preserveAdministrators(tx, human); err != nil {
		return err
	}
	return tx.Put("principals", id, human)
}
