package session

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/access"
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

func TestPrepareAbsentDeletionFencesProvisioningAndSupportsZeroScopeHuman(t *testing.T) {
	m, idp := portalSessionFixture(t)
	defer m.Close()
	portalOnly, err := m.SetHuman(idp.issuer(), "portal-only", []string{}, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := m.state.Update(func(tx *store.Tx) error { return tx.Delete("principals", portalOnly.ID) }); err != nil {
		t.Fatal(err)
	}
	requestID := "10000000-0000-4000-8000-000000000001"
	intent, err := m.PrepareAbsentDeletion(portalOnly.ID, "idp-only", requestID)
	if err != nil || intent.Principal != portalOnly.ID || intent.Username != "idp-only" || intent.Generation != 2 {
		t.Fatalf("prepare absent = %#v, %v", intent, err)
	}
	if _, err := m.SetHuman(idp.issuer(), "portal-only", []string{}, false); !errors.Is(err, ErrConflict) {
		t.Fatalf("fence allowed provisioning: %v", err)
	}
	if err := m.FinalizeDeletion(requestID, portalOnly.ID, 2); err != nil {
		t.Fatal(err)
	}
	fresh, err := m.SetHuman(idp.issuer(), "portal-only", []string{}, false)
	if err != nil || fresh.ID != portalOnly.ID || fresh.Generation != 1 {
		t.Fatalf("finalized tombstone retained identity: %#v, %v", fresh, err)
	}
}

func TestPrepareDeletionDisablesEnabledPortalOnlyHuman(t *testing.T) {
	m, idp := portalSessionFixture(t)
	defer m.Close()
	human, err := m.SetHuman(idp.issuer(), "portal-member", []string{}, false)
	if err != nil {
		t.Fatal(err)
	}
	requestID := "20000000-0000-4000-8000-000000000001"
	intent, err := m.PrepareDeletion(human.ID, human.Generation, requestID)
	if err != nil || intent.Generation != 2 {
		t.Fatalf("prepare portal-only human = %#v, %v", intent, err)
	}
	current, err := m.InspectHuman(human.ID)
	if err != nil || !current.Disabled || current.Generation != 2 || len(current.Resources) != 0 {
		t.Fatalf("portal-only human was not fenced: %#v, %v", current, err)
	}
}

func TestPrepareAbsentDeletionRollsBackTombstoneOnFailure(t *testing.T) {
	m, state, _, _, _ := sessionFixture(t, 8, 2)
	defer m.Close()
	principal := "human:0000000000000000000000000000000000000000000000000000000000000001"
	if err := state.Update(func(tx *store.Tx) error {
		return tx.Put("transactions", "human-delete:broken", json.RawMessage(`{"invalid":true}`))
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := m.PrepareAbsentDeletion(principal, "idp-only", "30000000-0000-4000-8000-000000000001"); err == nil {
		t.Fatal("malformed retained deletion record accepted")
	}
	if err := state.View(func(tx *store.Tx) error {
		var human Human
		if !errors.Is(tx.Get("principals", principal, &human), store.ErrMissing) {
			t.Fatal("failed prepare retained tombstone")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestInspectHumanFailsClosedOnOrphanNativeReferences(t *testing.T) {
	m, state, _, _, _ := sessionFixture(t, 8, 2)
	defer m.Close()
	principal := "human:0000000000000000000000000000000000000000000000000000000000000000"
	for _, orphan := range []struct {
		bucket string
		id     string
		value  any
	}{
		{"sessions", "session:orphan-session", Session{ID: "orphan-session", Principal: principal}},
		{"api_keys", "orphan-key", access.Key{ID: "orphan-key", DeviceHuman: principal}},
		{"transactions", "device:orphan-device", struct {
			ID      string `json:"id"`
			HumanID string `json:"human_id"`
		}{ID: "device:orphan-device", HumanID: principal}},
		{"transactions", "administration-action:orphan-action", struct {
			Actor  string `json:"actor"`
			Target string `json:"target"`
		}{Actor: principal}},
	} {
		if err := state.Update(func(tx *store.Tx) error { return tx.Put(orphan.bucket, orphan.id, orphan.value) }); err != nil {
			t.Fatal(err)
		}
		if _, err := m.InspectHuman(principal); !errors.Is(err, ErrUnavailable) {
			t.Fatalf("orphan %s was reported absent: %v", orphan.id, err)
		}
		if err := state.Update(func(tx *store.Tx) error { return tx.Delete(orphan.bucket, orphan.id) }); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := m.InspectHuman(principal); !errors.Is(err, store.ErrMissing) {
		t.Fatalf("cleanly absent principal = %v, want ErrMissing", err)
	}
}
