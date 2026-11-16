package session

import (
	"context"
	"fmt"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func pendingTransactions(t *testing.T, manager *Manager) int {
	t.Helper()
	count := 0
	if err := manager.state.View(func(tx *store.Tx) error {
		records, err := tx.List("transactions", transactionPrefix, maxTransactionsStored)
		count = len(records)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	return count
}

// An anonymous client minting fresh bindings far beyond the global pool must
// not prevent a different client from starting and completing sign-in, even
// when the flood continues while that sign-in is at the IdP.
func TestLoginFloodFromOneClientDoesNotBlockAnother(t *testing.T) {
	manager, _, idp, _, _ := sessionFixture(t, 128, 8)
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	flood := func(n int) {
		for i := 0; i < n; i++ {
			binding, _ := NewBinding()
			if _, err := manager.Begin("dash", "/", binding, "203.0.113.9"); err != nil {
				t.Fatalf("flooding client was refused instead of replacing its own flow: %v", err)
			}
		}
	}
	flood(300)
	victim, _ := NewBinding()
	challenge, err := manager.Begin("dash", "/", victim, "198.51.100.7")
	if err != nil {
		t.Fatalf("flood from one client blocked another client's sign-in: %v", err)
	}
	flood(300)
	query := configureToken(idp, challenge, tokenClaims{Subject: "person-1"})
	completion, err := manager.Complete(context.Background(), Callback{Host: "dash.example.test", State: query.Get("state"), Code: "code", Binding: victim})
	if err != nil || completion.Cookie == "" {
		t.Fatalf("flood evicted another client's pending sign-in: %v", err)
	}
	if got := pendingTransactions(t, manager); got > manager.perClient() {
		t.Fatalf("one client holds %d pending flows, want <= %d", got, manager.perClient())
	}
}

// A browser that retries login with its existing binding replaces its own
// flows; it never grows the pool past the per-browser bound and its latest
// attempt completes.
func TestLoginRetryReusesBrowserBinding(t *testing.T) {
	manager, _, idp, _, _ := sessionFixture(t, 128, 8)
	if _, err := manager.SetHuman(idp.issuer(), "person-1", []string{"dash"}, false); err != nil {
		t.Fatal(err)
	}
	binding, _ := NewBinding()
	var latest Challenge
	for i := 0; i < 50; i++ {
		var err error
		if latest, err = manager.Begin("dash", "/", binding, fmt.Sprintf("roaming-%d", i)); err != nil {
			t.Fatal(err)
		}
	}
	if got := pendingTransactions(t, manager); got != 8 {
		t.Fatalf("retrying browser holds %d flows, want 8", got)
	}
	if result := complete(t, manager, idp, latest, "person-1"); result.Cookie == "" {
		t.Fatal("latest retry did not complete")
	}
}

// Pending state for any mix of clients is bounded by the global pool.
func TestLoginPendingStateIsBounded(t *testing.T) {
	manager, _, _, _, _ := sessionFixture(t, 128, 8)
	for i := 0; i < 400; i++ {
		binding, _ := NewBinding()
		if _, err := manager.Begin("dash", "/", binding, fmt.Sprintf("client-%d", i%40)); err != nil {
			t.Fatal(err)
		}
	}
	if got := pendingTransactions(t, manager); got != 128 {
		t.Fatalf("pending flows = %d, want the 128 bound", got)
	}
}
