package session

import (
	"errors"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func TestPendingDeletionRechecksCurrentOperatorPolicyAndDigest(t *testing.T) {
	m, state, idp, _, _ := sessionFixture(t, 8, 2)
	defer m.Close()
	human, err := m.SetHuman(idp.issuer(), "target", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	id := "00000000-0000-4000-8000-000000000001"
	if _, err := m.PrepareDeletion(human.ID, human.Generation, id); err != nil {
		t.Fatal(err)
	}
	// Simulate startup with a new immutable operator declaration after the
	// request was prepared by the previous gateway process.
	if err := m.ConfigureAdministration(config.BrowserAdministration{BrowserResource: "dash", Operators: []string{human.ID}}); err != nil {
		t.Fatal(err)
	}
	if intents, err := m.Deletions(); !errors.Is(err, ErrDenied) || len(intents) != 0 {
		t.Fatalf("new operator exported: %#v %v", intents, err)
	}
	m.administration = nil
	if err := state.Update(func(tx *store.Tx) error {
		var intent Deletion
		if err := tx.Get("transactions", deletionPrefix+id, &intent); err != nil {
			return err
		}
		intent.Digest = strings.Repeat("0", 64)
		return tx.Put("transactions", deletionPrefix+id, intent)
	}); err != nil {
		t.Fatal(err)
	}
	if intents, err := m.Deletions(); err == nil || len(intents) != 0 {
		t.Fatal("drifted digest exported")
	}
}

func TestCompletedDeletionDoesNotBlockAfterAuthorityReset(t *testing.T) {
	m, state, idp, _, _ := sessionFixture(t, 8, 2)
	defer m.Close()
	h, err := m.SetHuman(idp.issuer(), "first", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	id := "00000000-0000-4000-8000-000000000011"
	if _, err := m.PrepareDeletion(h.ID, h.Generation, id); err != nil {
		t.Fatal(err)
	}
	if err := m.FinalizeDeletion(id, h.ID, 2); err != nil {
		t.Fatal(err)
	}
	if err := state.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if pending, err := m.Deletions(); err != nil || len(pending) != 0 {
		t.Fatalf("old completed receipt blocked worker: %#v %v", pending, err)
	}
	next, err := m.SetHuman(idp.issuer(), "second", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	nextID := "00000000-0000-4000-8000-000000000012"
	if _, err := m.PrepareDeletion(next.ID, next.Generation, nextID); err != nil {
		t.Fatal(err)
	}
	if pending, err := m.Deletions(); err != nil || len(pending) != 1 {
		t.Fatalf("new intent blocked: %#v %v", pending, err)
	}
	if err := state.View(func(tx *store.Tx) error {
		var old Deletion
		if !errors.Is(tx.Get("transactions", deletionPrefix+id, &old), store.ErrMissing) {
			t.Fatal("obsolete receipt not pruned")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
