package session

import (
	"errors"
	"reflect"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
)

func TestApplicationRolesInvalidateSessionsAndPreserveOldClientUpdates(t *testing.T) {
	manager, _, idp, _, _ := sessionFixture(t, 8, 2)
	defer manager.Close()
	roles := map[string]string{"dash": "admin"}
	human, err := manager.SetHuman(idp.issuer(), "role-holder", []string{"dash"}, false, roles)
	if err != nil {
		t.Fatal(err)
	}
	binding, _ := NewBinding()
	challenge, err := manager.Begin("dash", "/", binding)
	if err != nil {
		t.Fatal(err)
	}
	issued := complete(t, manager, idp, challenge, "role-holder")
	admitted, err := manager.Authenticate(issued.Cookie, "dash.example.test")
	if err != nil || admitted.ApplicationRole != "admin" {
		t.Fatalf("role admission = %#v, %v", admitted, err)
	}
	current, err := manager.CurrentHuman(admitted)
	if err != nil || current.ApplicationRoles["dash"] != "admin" {
		t.Fatalf("current human = %#v, %v", current, err)
	}
	current.Resources[0] = "changed"
	current.ApplicationRoles["dash"] = "member"
	if err := manager.Check(admitted); err != nil {
		t.Fatal("caller mutated returned current human", err)
	}
	updated, err := manager.SetHuman(idp.issuer(), "role-holder", []string{"dash"}, false, map[string]string{"dash": "member"})
	if err != nil || updated.Generation != human.Generation+1 {
		t.Fatalf("role update = %#v, %v", updated, err)
	}
	if err := manager.Check(admitted); err == nil {
		t.Fatal("role downgrade left prior session admitted")
	}
	if _, err := manager.Authenticate(issued.Cookie, "dash.example.test"); err == nil {
		t.Fatal("role downgrade left prior cookie admitted")
	}
	retained, err := manager.SetHuman(idp.issuer(), "role-holder", []string{"dash"}, false)
	if err != nil || retained.ApplicationRoles["dash"] != "member" {
		t.Fatalf("role omission widened or erased role: %#v, %v", retained, err)
	}
	for _, invalid := range []map[string]string{{"dash": "operator"}, {"other": "member"}} {
		if _, err := manager.SetHuman(idp.issuer(), "role-holder", []string{"dash"}, false, invalid); err == nil {
			t.Fatal("invalid application role accepted")
		}
	}
	if manager.AccountURL() != idp.issuer() {
		t.Fatal("account URL was not the configured issuer")
	}
}

func TestSuspendHumanRevokesOldBrowserAndDeviceAuthority(t *testing.T) {
	manager, _, idp, _, _ := sessionFixture(t, 8, 2)
	defer manager.Close()
	operator, err := manager.SetHuman(idp.issuer(), "operator", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	roles := map[string]string{"dash": "admin", "other": "member"}
	target, err := manager.SetHuman(idp.issuer(), "target", []string{"dash", "other"}, false, roles)
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.ConfigureAdministration(config.BrowserAdministration{BrowserResource: "dash", Operators: []string{operator.ID}}); err != nil {
		t.Fatal(err)
	}
	binding, err := NewBinding()
	if err != nil {
		t.Fatal(err)
	}
	issued := complete(t, manager, idp, mustBegin(t, manager, binding, "dash"), "target")
	admitted, err := manager.Authenticate(issued.Cookie, issued.Host)
	if err != nil {
		t.Fatal(err)
	}
	suspended, err := manager.SuspendHuman(idp.issuer(), "target")
	if err != nil {
		t.Fatal(err)
	}
	if !suspended.Disabled || suspended.Generation != target.Generation+1 || !reflect.DeepEqual(suspended.Resources, target.Resources) || !reflect.DeepEqual(suspended.ApplicationRoles, roles) {
		t.Fatalf("suspension changed policy: %#v", suspended)
	}
	if _, err := manager.Authenticate(issued.Cookie, issued.Host); err == nil || manager.Check(admitted) == nil || manager.CheckPrincipal(target.ID, target.Generation) == nil || manager.CheckDeviceSession(admitted.SessionID, admitted.SessionGeneration, target.ID, target.Generation) == nil {
		t.Fatal("suspension left browser or device authority active")
	}
	unchanged, err := manager.SuspendHuman(idp.issuer(), "target")
	if err != nil || unchanged.Generation != suspended.Generation || !reflect.DeepEqual(unchanged.Resources, target.Resources) || !reflect.DeepEqual(unchanged.ApplicationRoles, roles) {
		t.Fatalf("already disabled suspension changed policy: %#v, %v", unchanged, err)
	}
	reenabled, err := manager.SetHuman(idp.issuer(), "target", target.Resources, false)
	if err != nil || reenabled.Generation != suspended.Generation+1 || !reflect.DeepEqual(reenabled.ApplicationRoles, roles) {
		t.Fatalf("re-enable changed policy: %#v, %v", reenabled, err)
	}
	if _, err := manager.Authenticate(issued.Cookie, issued.Host); err == nil || manager.CheckPrincipal(target.ID, target.Generation) == nil || manager.CheckDeviceSession(admitted.SessionID, admitted.SessionGeneration, target.ID, target.Generation) == nil {
		t.Fatal("re-enable revived old browser or device authority")
	}
	unknown, err := manager.SuspendHuman(idp.issuer(), "unknown")
	if err != nil || unknown.Generation != 0 || unknown.Disabled {
		t.Fatalf("unknown suspension provisioned a grant: %#v, %v", unknown, err)
	}
	newHuman, err := manager.SetHuman(idp.issuer(), "unknown", []string{"dash"}, false)
	if err != nil || newHuman.ID != unknown.ID || newHuman.Generation != 1 || newHuman.Disabled {
		t.Fatalf("unknown suspension left a grant: %#v, %v", newHuman, err)
	}
	if _, err := manager.SuspendHuman("https://wrong.example.test", "target"); !errors.Is(err, ErrDenied) {
		t.Fatalf("unconfigured issuer accepted: %v", err)
	}
}

func TestSuspendHumanPreservesLastConfiguredOperator(t *testing.T) {
	manager, _, idp, _, _ := sessionFixture(t, 8, 2)
	defer manager.Close()
	operator, err := manager.SetHuman(idp.issuer(), "operator", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.ConfigureAdministration(config.BrowserAdministration{BrowserResource: "dash", Operators: []string{operator.ID}}); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.SuspendHuman(idp.issuer(), "operator"); !errors.Is(err, ErrConflict) {
		t.Fatalf("last configured operator suspended: %v", err)
	}
}
