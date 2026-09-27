package session

import (
	"errors"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"testing"
)

func TestRouterFenceDeleteRecreateAndRecovery(t *testing.T) {
	m, idp := portalSessionFixture(t)
	defer m.Close()
	login := func() (Human, Completion) {
		username := "router-person"
		human, err := m.SetHumanWithUsername(idp.issuer(), "router-person", &username, nil, false)
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
	if name, err := m.RouterAccountName(human.ID, human.Generation, fence); err != nil || name != "router-person" {
		t.Fatal("current username unavailable", err)
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
	if name, err := m.RouterAccountName(human.ID, 1, fence); err == nil || name != "" {
		t.Fatal("stale record labeled as recreated account")
	}
	if fence == newFence || m.CheckRouterPrincipal(human.ID, 1, fence) == nil {
		t.Fatal("deleted credential resurrected")
	}
	if m.CheckRouterPrincipal(human.ID, 1, newFence) != nil {
		t.Fatal("replacement account denied")
	}
	for _, username := range []string{"invalid username", "router-person"} {
		if err := m.state.Update(func(tx *store.Tx) error {
			var record Human
			if err := tx.Get("principals", human.ID, &record); err != nil {
				return err
			}
			record.Username = username
			return tx.Put("principals", human.ID, record)
		}); err != nil {
			t.Fatal(err)
		}
		name, err := m.RouterAccountName(human.ID, 1, newFence)
		if username == "invalid username" {
			if name != "" || !errors.Is(err, ErrUnavailable) {
				t.Fatal("malformed username exposed", err)
			}
		} else if err != nil || name != username {
			t.Fatal("restored username unavailable", err)
		}
	}
	if err := m.state.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if m.CheckRouterPrincipal(human.ID, 1, newFence) == nil {
		t.Fatal("recovery retained old fence")
	}
}
