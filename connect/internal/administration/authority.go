// Package administration is the gateway's browser-facing authority boundary.
// It never proxies the local administration socket or exports credentials.
package administration

import (
	"errors"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/device"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

var (
	ErrDenied      = errors.New("administration denied")
	ErrConflict    = errors.New("administration state changed")
	ErrUnavailable = errors.New("administration unavailable")
	ErrInvalid     = errors.New("invalid administration request")
)

type Authority struct {
	state    *store.Store
	sessions *session.Manager
	settings config.BrowserAdministration
	rules    map[string]config.Rule
	bindings map[string]device.Binding
}

type Inventory struct {
	Kind       string  `json:"kind"`
	Items      []any   `json:"items"`
	NextCursor *string `json:"next_cursor"`
}

func New(state *store.Store, sessions *session.Manager, declaration config.Gateway) (*Authority, error) {
	if state == nil || sessions == nil || declaration.Validate() != nil || declaration.BrowserAdministration == nil {
		return nil, ErrInvalid
	}
	settings := *declaration.BrowserAdministration
	settings.Operators = append([]string(nil), settings.Operators...)
	if sessions.ConfigureAdministration(settings) != nil {
		return nil, ErrInvalid
	}
	a := &Authority{state: state, sessions: sessions, settings: settings, rules: map[string]config.Rule{}, bindings: map[string]device.Binding{}}
	for _, resource := range declaration.Resources {
		a.rules[resource.Rule.ID] = resource.Rule
	}
	for _, declared := range declaration.DeviceAuthorizations {
		binding := device.Binding{Browser: a.rules[declared.BrowserResource], API: a.rules[declared.APIResource], Methods: append([]string(nil), declared.Methods...), Label: declared.Label, Principals: declared.Principals}
		a.bindings[device.BindingHash(binding)] = binding
	}
	return a, nil
}

func (a *Authority) authorize(tx *store.Tx, admitted session.Admission) error {
	if admitted.Resource != a.settings.BrowserResource {
		return ErrDenied
	}
	operator := false
	for _, id := range a.settings.Operators {
		if id == admitted.Principal {
			operator = true
			break
		}
	}
	if !operator || a.sessions.CheckTx(tx, admitted) != nil {
		return ErrDenied
	}
	return nil
}
