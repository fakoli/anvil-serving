package tunnelgate

import (
	"encoding/json"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
)

type authority struct{ revoked atomic.Bool }

func (a *authority) Check(identity.Installation, string) error {
	if a.revoked.Load() {
		return ErrDenied
	}
	return nil
}

func declaration(t *testing.T) config.Gateway {
	t.Helper()
	f, err := os.Open("../../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	g, err := config.ReadGateway(f)
	if err != nil {
		t.Fatal(err)
	}
	return g
}

func installed() identity.Installation {
	return identity.Installation{ID: "origin-a", Role: "connector", Epoch: strings.Repeat("a", 64), Generation: 1, Fingerprint: "synthetic", PublicKey: json.RawMessage(`{"synthetic":true}`), Resources: []string{"router"}}
}

func TestTransportAdmissionRefreshAndActivePermission(t *testing.T) {
	now := time.Now()
	a := &authority{}
	l, err := NewLeases(declaration(t), a, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	raw, until, err := l.Issue(installed(), "router")
	if err != nil || !until.Equal(now.Add(time.Minute)) {
		t.Fatal("initial issuance failed", err)
	}
	admitted, err := l.Authenticate(raw)
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(50 * time.Second)
	fresh, _, err := l.Issue(installed(), "router")
	if err != nil || fresh == raw {
		t.Fatal("refresh did not replace admission credential")
	}
	now = now.Add(10 * time.Second)
	if _, err := l.Authenticate(raw); err == nil {
		t.Fatal("expired admission token revived by refresh")
	}
	if l.Check(admitted) != nil {
		t.Fatal("fresh same-key control permission interrupted active stream")
	}
	if _, err := l.Authenticate(fresh); err != nil {
		t.Fatal(err)
	}
	a.revoked.Store(true)
	if l.Check(admitted) == nil {
		t.Fatal("revocation left active permission")
	}
	if _, err := l.Authenticate(fresh); err == nil {
		t.Fatal("revocation left new admission")
	}
}

func TestTransportGenerationCannotExtendOldPermission(t *testing.T) {
	now := time.Now()
	l, _ := NewLeases(declaration(t), &authority{}, func() time.Time { return now })
	old, _, _ := l.Issue(installed(), "router")
	admitted, _ := l.Authenticate(old)
	now = now.Add(50 * time.Second)
	rotated := installed()
	rotated.Generation++
	rotated.Fingerprint = "new-key"
	fresh, _, err := l.Issue(rotated, "router")
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(10 * time.Second)
	if l.Check(admitted) == nil {
		t.Fatal("new generation renewed old permission")
	}
	if _, err := l.Authenticate(fresh); err != nil {
		t.Fatal("new generation denied", err)
	}
	now = now.Add(-time.Minute)
	if _, err := l.Authenticate(fresh); err == nil {
		t.Fatal("clock rollback accepted token")
	}
	now = now.Add(time.Minute)
	if _, err := l.Authenticate(fresh); err == nil {
		t.Fatal("swept authority reactivated")
	}
}

func TestExpiredPermissionCannotReviveBeforePollerNotices(t *testing.T) {
	now := time.Now()
	l, _ := NewLeases(declaration(t), &authority{}, func() time.Time { return now })
	raw, _, _ := l.Issue(installed(), "router")
	old, _ := l.Authenticate(raw)
	now = now.Add(TransportLeaseLifetime)
	fresh, _, err := l.Issue(installed(), "router")
	if err != nil {
		t.Fatal(err)
	}
	if l.Check(old) == nil {
		t.Fatal("expired active permission revived without a fresh stream admission")
	}
	if _, err := l.Authenticate(fresh); err != nil {
		t.Fatal(err)
	}
}

func TestTransportIssuanceScopeAndCapacity(t *testing.T) {
	l, _ := NewLeases(declaration(t), &authority{}, nil)
	for _, i := range []identity.Installation{{ID: "other", Role: "connector"}, {ID: "origin-a", Role: "client"}} {
		if _, _, err := l.Issue(i, "router"); err == nil {
			t.Fatal("wrong installation role or resource owner issued")
		}
	}
	if _, _, err := l.Issue(installed(), "unknown"); err == nil {
		t.Fatal("unknown resource issued")
	}
	var first string
	for n := 0; n < maximumTokens; n++ {
		raw, _, err := l.Issue(installed(), "router")
		if err != nil {
			t.Fatal(n, err)
		}
		if n == 0 {
			first = raw
		}
	}
	if _, _, err := l.Issue(installed(), "router"); err == nil {
		t.Fatal("token capacity exceeded")
	}
	if _, err := l.Authenticate(first); err != nil {
		t.Fatal("live token evicted", err)
	}
	fresh, _ := NewLeases(declaration(t), &authority{}, nil)
	if _, err := fresh.Authenticate(first); err == nil {
		t.Fatal("restart retained credential")
	}
	for _, raw := range []string{"", first + "x", strings.ToUpper(first), first[:80] + "!", "act1." + strings.Repeat("z", 32) + "." + strings.Repeat("a", 43)} {
		if _, err := l.Authenticate(raw); err == nil {
			t.Fatal("malformed token accepted")
		}
	}
}
