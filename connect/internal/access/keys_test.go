package access

import (
	"bytes"
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func keyFixture(t *testing.T) (*Keys, *store.Store, *time.Time, string) {
	t.Helper()
	directory := filepath.Join(t.TempDir(), "authority")
	now := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	s, err := store.Open(directory, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	file, err := os.Open("../../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	g, err := config.ReadGateway(file)
	if err != nil {
		t.Fatal(err)
	}
	rule := g.Resources[0].Rule
	other := rule
	other.ID = "second"
	other.Host = "second.example.test"
	k, err := NewKeys(s, []config.Rule{rule, other})
	if err != nil {
		t.Fatal(err)
	}
	if err := k.SetPrincipal("owner", []Grant{{"router", []string{"GET", "POST"}}}, false); err != nil {
		t.Fatal(err)
	}
	return k, s, &now, directory
}

func issue(t *testing.T, k *Keys) (string, Key) {
	t.Helper()
	raw, key, err := k.Issue("owner", []Grant{{"router", []string{"POST"}}}, 0)
	if err != nil {
		t.Fatal(err)
	}
	return raw, key
}

func TestKeyScopeAndSecretStorage(t *testing.T) {
	k, s, now, directory := keyFixture(t)
	raw, key := issue(t, k)
	if key.ExpiresAt.Sub(*now) != DefaultKeyLifetime {
		t.Fatal("default lifetime changed")
	}
	admitted, err := k.Authenticate(raw, "router", "POST")
	if err != nil {
		t.Fatal(err)
	}
	if err := k.Check(admitted); err != nil {
		t.Fatal(err)
	}
	for _, request := range []struct{ raw, resource, method string }{
		{raw, "router", "GET"}, {raw, "second", "POST"}, {raw, "router", "DELETE"},
		{raw + "x", "router", "POST"}, {"ac1.invalid", "router", "POST"},
		{raw[:79] + "!", "router", "POST"},
	} {
		if _, err := k.Authenticate(request.raw, request.resource, request.method); err == nil {
			t.Fatal("ungranted or invalid key accepted")
		}
	}
	if _, _, err := k.Issue("owner", []Grant{{"second", []string{"POST"}}}, 0); err == nil {
		t.Fatal("principal grant widened")
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := os.ReadFile(filepath.Join(directory, "state.db"))
	if err != nil {
		t.Fatal(err)
	}
	secret := strings.Split(raw, ".")[2]
	decoded, _ := base64.RawURLEncoding.DecodeString(secret)
	if bytes.Contains(database, []byte(raw)) || bytes.Contains(database, []byte(secret)) || bytes.Contains(database, decoded) {
		t.Fatal("raw credential persisted")
	}
}

func TestExpiryRevocationAndPrincipalGeneration(t *testing.T) {
	for _, cause := range []string{"expiry", "clock-before-issue", "key-revoke", "principal-disable", "principal-regrant", "epoch"} {
		t.Run(cause, func(t *testing.T) {
			k, s, now, _ := keyFixture(t)
			raw, key := issue(t, k)
			admitted, err := k.Authenticate(raw, "router", "POST")
			if err != nil {
				t.Fatal(err)
			}
			switch cause {
			case "expiry":
				*now = key.ExpiresAt
			case "clock-before-issue":
				*now = now.Add(-time.Second)
			case "key-revoke":
				if err := k.Revoke(key.ID); err != nil {
					t.Fatal(err)
				}
			case "principal-disable":
				if err := k.SetPrincipal("owner", []Grant{{"router", []string{"POST"}}}, true); err != nil {
					t.Fatal(err)
				}
			case "principal-regrant":
				if err := k.SetPrincipal("owner", []Grant{{"router", []string{"POST"}}}, true); err != nil {
					t.Fatal(err)
				}
				if err := k.SetPrincipal("owner", []Grant{{"router", []string{"POST"}}}, false); err != nil {
					t.Fatal(err)
				}
			case "epoch":
				if err := s.ResetAuthority(); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := k.Authenticate(raw, "router", "POST"); err == nil {
				t.Fatal("stale key accepted")
			}
			if err := k.Check(admitted); err == nil {
				t.Fatal("stale admission snapshot accepted")
			}
		})
	}
}

func TestKeyRestartPreservesRevocation(t *testing.T) {
	k, s, now, directory := keyFixture(t)
	good, _ := issue(t, k)
	bad, key := issue(t, k)
	if err := k.Revoke(key.ID); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := store.Open(directory, func() time.Time { return *now })
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	var rules []config.Rule
	for _, rule := range k.rules {
		rules = append(rules, rule)
	}
	fresh, err := NewKeys(reopened, rules)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fresh.Authenticate(good, "router", "POST"); err != nil {
		t.Fatal(err)
	}
	if _, err := fresh.Authenticate(bad, "router", "POST"); err == nil {
		t.Fatal("restart reactivated revoked key")
	}
	if err := reopened.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if _, err := fresh.Authenticate(good, "router", "POST"); err == nil {
		t.Fatal("epoch change left old key active")
	}
	newRaw, _ := issue(t, fresh)
	if _, err := fresh.Authenticate(newRaw, "router", "POST"); err != nil {
		t.Fatal(err)
	}
}

func TestGrantAndLifetimeBounds(t *testing.T) {
	k, _, _, _ := keyFixture(t)
	for _, grants := range [][]Grant{nil, {{"unknown", []string{"GET"}}}, {{"router", nil}}, {{"router", []string{"GET", "GET"}}}, {{"router", []string{"CONNECT"}}}, {{"router", []string{"GET"}}, {"router", []string{"POST"}}}} {
		if _, _, err := k.Issue("owner", grants, 0); err == nil {
			t.Fatal("invalid grant accepted")
		}
	}
	for _, lifetime := range []time.Duration{-1, time.Second, MaximumKeyLifetime + time.Second} {
		if _, _, err := k.Issue("owner", []Grant{{"router", []string{"POST"}}}, lifetime); err == nil {
			t.Fatal("invalid lifetime accepted")
		}
	}
	if _, _, err := k.Issue("unknown", []Grant{{"router", []string{"POST"}}}, 0); err == nil {
		t.Fatal("unknown principal accepted")
	}
}

func TestConcurrentKeyIssuance(t *testing.T) {
	k, _, _, _ := keyFixture(t)
	var wait sync.WaitGroup
	ids := make(chan string, 16)
	for i := 0; i < 16; i++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			raw, key, err := k.Issue("owner", []Grant{{"router", []string{"POST"}}}, 0)
			if err != nil {
				t.Error(err)
				return
			}
			if _, err := k.Authenticate(raw, "router", "POST"); err != nil {
				t.Error(err)
			}
			ids <- key.ID
		}()
	}
	wait.Wait()
	close(ids)
	seen := map[string]bool{}
	for id := range ids {
		if seen[id] {
			t.Fatal("repeated key identifier")
		}
		seen[id] = true
	}
	if len(seen) != 16 {
		t.Fatal("issuance lost a transaction")
	}
}
