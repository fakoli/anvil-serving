package administration

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func TestPermanentDeletionFencesAndPurgesOnlyTarget(t *testing.T) {
	f := setup(t)
	f.a.settings.UserDeletion = true
	name := "developer"
	human, err := f.sessions.SetHumanWithUsername(f.issuer, "target", &name, f.target.Resources, false)
	if err != nil {
		t.Fatal(err)
	}
	targetSession := f.seedSession(t, human, strings.Repeat("3", 32))
	deviceKey := strings.Repeat("a", 32)
	manualKey := strings.Repeat("b", 32)
	if err := f.state.Update(func(tx *store.Tx) error {
		if err := tx.Put("api_keys", deviceKey, access.Key{ID: deviceKey, DeviceHuman: human.ID}); err != nil {
			return err
		}
		return tx.Put("api_keys", manualKey, access.Key{ID: manualKey, Principal: "sdk-principal"})
	}); err != nil {
		t.Fatal(err)
	}
	mutation := Mutation{Action: "human-delete", RequestID: request(70), ExpectedGeneration: "2", Principal: human.ID}
	if err := f.a.Mutate(context.Background(), f.ownerSession, mutation); err != nil {
		t.Fatal(err)
	}
	if err := f.sessions.Check(targetSession); err == nil {
		t.Fatal("old session accepted")
	}
	if _, err := f.sessions.SetHuman(f.issuer, "target", human.Resources, false); !errors.Is(err, session.ErrConflict) {
		t.Fatalf("re-enabled deleting user: %v", err)
	}
	if err := f.a.Mutate(context.Background(), f.ownerSession, mutation); err != nil {
		t.Fatalf("lost-response retry: %v", err)
	}
	intents, err := f.sessions.Deletions()
	if err != nil || len(intents) != 1 || intents[0].Username != name || intents[0].Generation != 3 {
		t.Fatalf("intents = %#v, %v", intents, err)
	}
	if err := f.sessions.FinalizeDeletion(mutation.RequestID, human.ID, 2); !errors.Is(err, session.ErrConflict) {
		t.Fatalf("stale finalize: %v", err)
	}
	if err := f.sessions.FinalizeDeletion(mutation.RequestID, human.ID, 3); err != nil {
		t.Fatal(err)
	}
	if err := f.a.Mutate(context.Background(), f.ownerSession, mutation); err != nil {
		t.Fatalf("completed replay: %v", err)
	}
	if err := f.state.View(func(tx *store.Tx) error {
		for _, pair := range [][2]string{{"principals", human.ID}, {"sessions", "session:" + targetSession.SessionID}, {"api_keys", deviceKey}} {
			var value any
			if !errors.Is(tx.Get(pair[0], pair[1], &value), store.ErrMissing) {
				t.Fatalf("retained %v", pair)
			}
		}
		var key access.Key
		if tx.Get("api_keys", manualKey, &key) != nil || key.Principal != "sdk-principal" {
			t.Fatal("manual key altered")
		}
		rows, err := tx.List("transactions", "human-delete:", 1024)
		if err != nil {
			return err
		}
		for _, row := range rows {
			if strings.Contains(string(row.Value), human.ID) || strings.Contains(string(row.Value), name) {
				t.Fatal("completed receipt retains identity")
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := f.sessions.Check(f.ownerSession); err != nil {
		t.Fatal("unrelated operator session changed", err)
	}
	fresh, err := f.sessions.SetHumanWithUsername(f.issuer, "fresh-subject", &name, human.Resources, false)
	if err != nil || fresh.ID == human.ID || fresh.Generation != 1 {
		t.Fatalf("username reuse: %#v %v", fresh, err)
	}
	if err := f.sessions.Check(targetSession); err == nil {
		t.Fatal("old session resurrected")
	}
}

func TestDeletionRejectsMembersOperatorsAndDrift(t *testing.T) {
	f := setup(t)
	makeRequest := func(id string, n int) Mutation {
		return Mutation{Action: "human-delete", RequestID: request(n), ExpectedGeneration: "1", Principal: id}
	}
	if err := f.a.Mutate(context.Background(), f.ownerSession, makeRequest(f.target.ID, 80)); !errors.Is(err, ErrDenied) {
		t.Fatalf("disabled feature: %v", err)
	}
	f.a.settings.UserDeletion = true
	member := f.seedSession(t, f.target, strings.Repeat("4", 32))
	if err := f.a.Mutate(context.Background(), member, makeRequest(f.other.ID, 81)); !errors.Is(err, ErrDenied) {
		t.Fatalf("member deletion: %v", err)
	}
	if err := f.a.Mutate(context.Background(), f.ownerSession, makeRequest(f.other.ID, 82)); !errors.Is(err, ErrDenied) {
		t.Fatalf("operator deletion: %v", err)
	}
	if err := f.a.Mutate(context.Background(), f.ownerSession, makeRequest(f.owner.ID, 83)); err == nil {
		t.Fatal("self deletion")
	}
	stale := makeRequest(f.target.ID, 84)
	stale.ExpectedGeneration = "2"
	if err := f.a.Mutate(context.Background(), f.ownerSession, stale); !errors.Is(err, ErrConflict) {
		t.Fatalf("generation drift: %v", err)
	}
	if _, err := f.sessions.InspectHuman(f.target.ID); err != nil {
		t.Fatal(err)
	}
	// A malformed record aborts the whole purge transaction, retaining both the
	// principal and its sessions for safe operator recovery.
	if err := f.a.Mutate(context.Background(), f.ownerSession, makeRequest(f.target.ID, 85)); err != nil {
		t.Fatal(err)
	}
	if err := f.state.Update(func(tx *store.Tx) error {
		return tx.Put("sessions", "session:corrupt", json.RawMessage(`{"id":"mismatch"}`))
	}); err != nil {
		t.Fatal(err)
	}
	if err := f.sessions.FinalizeDeletion(request(85), f.target.ID, 2); err == nil {
		t.Fatal("corrupt namespace accepted")
	}
	if _, err := f.sessions.InspectHuman(f.target.ID); err != nil {
		t.Fatal("partial purge escaped transaction", err)
	}
}
