package session

import "testing"

func TestRouterFenceDeleteRecreateAndRecovery(t *testing.T) {
	m, idp := portalSessionFixture(t)
	defer m.Close()
	login := func() (Human, Completion) {
		human, err := m.SetHuman(idp.issuer(), "router-person", nil, false)
		if err != nil {
			t.Fatal(err)
		}
		binding, _ := NewBinding()
		challenge, err := m.BeginPortal(binding)
		if err != nil {
			t.Fatal(err)
		}
		return human, completeOnHost(t, m, idp, challenge, "router-person", "home.example.test")
	}
	human, completed := login()
	fence, err := m.RouterFence(completed.Cookie, "home.example.test")
	if err != nil {
		t.Fatal(err)
	}
	if m.CheckRouterPrincipal(human.ID, human.Generation, fence) != nil {
		t.Fatal("new fence denied")
	}
	if _, err := m.RouterFence(completed.Cookie, "dash.example.test"); err == nil {
		t.Fatal("foreign host accepted")
	}
	id := "00000000-0000-4000-8000-000000000090"
	if _, err := m.PrepareDeletion(human.ID, human.Generation, id); err != nil {
		t.Fatal(err)
	}
	if m.CheckRouterPrincipal(human.ID, human.Generation, fence) == nil {
		t.Fatal("deleting account accepted")
	}
	if err := m.FinalizeDeletion(id, human.ID, 2); err != nil {
		t.Fatal(err)
	}
	replacement, next := login()
	if replacement.ID != human.ID || replacement.Generation != human.Generation {
		t.Fatal("fixture did not recreate generation one")
	}
	newFence, err := m.RouterFence(next.Cookie, "home.example.test")
	if err != nil {
		t.Fatal(err)
	}
	if fence == newFence || m.CheckRouterPrincipal(human.ID, 1, fence) == nil {
		t.Fatal("deleted credential resurrected")
	}
	if m.CheckRouterPrincipal(human.ID, 1, newFence) != nil {
		t.Fatal("replacement account denied")
	}
	if err := m.state.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if m.CheckRouterPrincipal(human.ID, 1, newFence) == nil {
		t.Fatal("recovery retained old fence")
	}
}
