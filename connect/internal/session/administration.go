package session

import (
	"errors"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"maps"
	"math"
)

// OperatorEnsureResult records whether the local authority changed one
// configured browser-administration operator. It contains no credential or
// identity-provider material.
type OperatorEnsureResult struct {
	Principal  string `json:"principal"`
	Generation uint64 `json:"generation"`
	Changed    bool   `json:"changed"`
}

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

// EnsureOperatorsResource atomically grants one browser resource and its
// application role to every configured, enabled browser-administration
// operator. It never creates, enables, or otherwise repairs an operator.
func (m *Manager) EnsureOperatorsResource(resource, role string) ([]OperatorEnsureResult, error) {
	if m.administration == nil || role != "admin" {
		return nil, ErrDenied
	}
	rule, ok := m.rules[resource]
	if !ok || rule.Access != "browser" {
		return nil, ErrDenied
	}
	results := make([]OperatorEnsureResult, 0, len(m.administration.Operators))
	err := m.state.Update(func(tx *store.Tx) error {
		type pending struct {
			human Human
		}
		changes := make([]pending, 0, len(m.administration.Operators))
		results = results[:0]
		for _, id := range m.administration.Operators {
			var human Human
			if tx.Get("principals", id, &human) != nil || human.ID != id || human.Generation == 0 || human.Disabled || human.DeletionRequest != "" || (human.Username != "" && !ValidUsername(human.Username)) {
				return ErrDenied
			}
			resources, valid := m.validateResources(human.Resources)
			roles, rolesValid := m.validateApplicationRoles(resources, human.ApplicationRoles)
			if !valid || !rolesValid {
				return ErrUnavailable
			}
			present := hasResource(resources, resource)
			changed := !present || roles[resource] != role
			if changed {
				if human.Generation == math.MaxUint64 {
					return ErrUnavailable
				}
				if !present {
					resources = append(resources, resource)
				}
				resources, valid = m.validateResources(resources)
				if !valid {
					return ErrUnavailable
				}
				roles = maps.Clone(roles)
				if roles == nil {
					roles = map[string]string{}
				}
				roles[resource] = role
				human.Generation++
				human.Resources, human.ApplicationRoles = resources, roles
				changes = append(changes, pending{human: human})
			}
			results = append(results, OperatorEnsureResult{Principal: id, Generation: human.Generation, Changed: changed})
		}
		for _, change := range changes {
			if err := tx.Put("principals", change.human.ID, change.human); err != nil {
				return ErrUnavailable
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return results, nil
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
	if tx.Get("principals", id, &human) != nil || human.ID != id || human.Generation == 0 || (human.Username != "" && !ValidUsername(human.Username)) {
		return ErrDenied
	}
	if human.Generation != expected || human.DeletionRequest != "" {
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

// RevokeHumanSessions advances an existing human generation and records a
// monotonic transaction sequence floor. It leaves grants, roles, and disabled state
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
		if human.DeletionRequest != "" {
			return ErrConflict
		}
		if err != nil || human.ID != id || human.Generation == 0 || human.Generation == math.MaxUint64 || (human.Username != "" && !ValidUsername(human.Username)) {
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
		sequence, err := tx.NextSequence()
		if err != nil {
			return ErrUnavailable
		}
		human.Generation++
		if sequence > human.BrowserTransactionFloor {
			human.BrowserTransactionFloor = sequence
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
