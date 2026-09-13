package session

import (
	"testing"
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
