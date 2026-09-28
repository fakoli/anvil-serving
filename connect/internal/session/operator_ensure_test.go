package session

import (
	"errors"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func TestEnsureOperatorsResourceUnionsAtomicallyAndSkipsSatisfiedOperators(t *testing.T) {
	manager, state, idp, _, _ := sessionFixture(t, 8, 2)
	owner, err := manager.SetHuman(idp.issuer(), "owner", []string{"dash"}, false, map[string]string{"dash": "member"})
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.ConfigureAdministration(config.BrowserAdministration{BrowserResource: "dash", Operators: []string{owner.ID}}); err != nil {
		t.Fatal(err)
	}
	results, err := manager.EnsureOperatorsResource("other", "admin")
	if err != nil || len(results) != 1 || !results[0].Changed || results[0].Principal != owner.ID || results[0].Generation != owner.Generation+1 {
		t.Fatalf("initial union = %#v, %v", results, err)
	}
	var updated Human
	if err := state.View(func(tx *store.Tx) error { return tx.Get("principals", owner.ID, &updated) }); err != nil {
		t.Fatal(err)
	}
	if !hasResource(updated.Resources, "dash") || !hasResource(updated.Resources, "other") || updated.ApplicationRoles["dash"] != "member" || updated.ApplicationRoles["other"] != "admin" {
		t.Fatalf("union did not retain grants and roles: %#v", updated)
	}
	results, err = manager.EnsureOperatorsResource("other", "admin")
	if err != nil || len(results) != 1 || results[0].Changed || results[0].Generation != updated.Generation {
		t.Fatalf("satisfied union changed a principal: %#v, %v", results, err)
	}
}

func TestEnsureOperatorsResourceRejectsInvalidOperatorsWithoutPartialWrites(t *testing.T) {
	manager, state, idp, _, _ := sessionFixture(t, 8, 2)
	owner, err := manager.SetHuman(idp.issuer(), "owner", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	disabled, err := manager.SetHuman(idp.issuer(), "disabled", []string{"dash"}, true)
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.ConfigureAdministration(config.BrowserAdministration{BrowserResource: "dash", Operators: []string{owner.ID, disabled.ID}}); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.EnsureOperatorsResource("other", "admin"); !errors.Is(err, ErrDenied) {
		t.Fatalf("disabled operator accepted: %v", err)
	}
	var retained Human
	if err := state.View(func(tx *store.Tx) error { return tx.Get("principals", owner.ID, &retained) }); err != nil {
		t.Fatal(err)
	}
	if retained.Generation != owner.Generation || hasResource(retained.Resources, "other") {
		t.Fatalf("failed union partially wrote owner: %#v", retained)
	}
	if _, err := manager.EnsureOperatorsResource("dash", "member"); !errors.Is(err, ErrDenied) {
		t.Fatalf("non-admin role accepted: %v", err)
	}
}
