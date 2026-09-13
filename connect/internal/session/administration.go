package session

import (
	"errors"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"maps"
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
func (m *Manager) UpdateHumanTx(tx *store.Tx, id string, expected uint64, resources []string, disabled bool, roleInput ...map[string]string) error {
	if tx == nil || !config.ValidHumanID(id) || expected == 0 {
		return ErrDenied
	}
	if len(roleInput) > 1 {
		return ErrDenied
	}
	var applicationRoles map[string]string
	if len(roleInput) == 1 {
		applicationRoles = roleInput[0]
	}
	resources, ok := m.validateResources(resources)
	if !ok {
		return ErrDenied
	}
	if applicationRoles, ok = m.validateApplicationRoles(resources, applicationRoles); !ok {
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
	if human.ApplicationRoles != nil {
		if _, valid := m.validateApplicationRoles(human.Resources, human.ApplicationRoles); !valid {
			return ErrUnavailable
		}
	}
	if applicationRoles == nil {
		applicationRoles = retainApplicationRoles(human.ApplicationRoles, resources)
	}
	human.Generation++
	human.Resources = resources
	human.Disabled = disabled
	human.ApplicationRoles = applicationRoles
	if err := m.preserveAdministrators(tx, human); err != nil {
		return err
	}
	return tx.Put("principals", id, human)
}

// RevokeHumanSessions advances an existing human generation and sets a
// monotonic transaction cutoff. It leaves grants, roles, and disabled state
// unchanged; a missing human is intentionally a no-op.
func (m *Manager) RevokeHumanSessions(issuer, subject string) (Human, error) {
	if issuer != m.issuer {
		return Human{}, ErrDenied
	}
	id, ok := humanID(issuer, subject)
	if !ok {
		return Human{}, ErrDenied
	}
	result := Human{ID: id}
	err := m.state.Update(func(tx *store.Tx) error {
		var human Human
		err := tx.Get("principals", id, &human)
		if errors.Is(err, store.ErrMissing) {
			return nil
		}
		if err != nil || human.ID != id || human.Generation == 0 || human.Generation == math.MaxUint64 {
			return ErrUnavailable
		}
		resources, valid := m.validateResources(human.Resources)
		if !valid {
			return ErrUnavailable
		}
		roles, valid := m.validateApplicationRoles(resources, human.ApplicationRoles)
		if !valid {
			return ErrUnavailable
		}
		human.Resources, human.ApplicationRoles = resources, roles
		human.Generation++
		if !human.BrowserNotBefore.After(tx.Now()) {
			human.BrowserNotBefore = tx.Now()
		}
		if err := m.preserveAdministrators(tx, human); err != nil {
			return err
		}
		if err := tx.Put("principals", id, human); err != nil {
			return ErrUnavailable
		}
		result = human
		result.Resources = append([]string(nil), human.Resources...)
		result.ApplicationRoles = maps.Clone(human.ApplicationRoles)
		return nil
	})
	if err != nil {
		return Human{}, err
	}
	return result, nil
}
