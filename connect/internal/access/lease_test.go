package access

import (
	"strings"
	"testing"
	"time"
)

func TestDisconnectedLeaseExpiryAndRenewalBinding(t *testing.T) {
	now := time.Now()
	binding := LeaseBinding{Installation: "origin-a", Resource: "router", Epoch: strings.Repeat("a", 64), Generation: 1}
	lease, err := NewLease(binding, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	if lease.Check() == nil {
		t.Fatal("new connector was authorized before fresh control response")
	}
	requested := now
	now = now.Add(20 * time.Second) // a slow response cannot gain 45 seconds from receipt
	if err := lease.Renew(binding, 1, requested); err != nil {
		t.Fatal(err)
	}
	if lease.Check() != nil {
		t.Fatal("fresh lease denied")
	}
	if lease.Renew(binding, 1, now) == nil {
		t.Fatal("replayed response renewed lease")
	}
	wrong := binding
	wrong.Resource = "dashboard"
	if lease.Renew(wrong, 2, now) == nil {
		t.Fatal("lease crossed resource")
	}
	wrong = binding
	wrong.Generation++
	if lease.Renew(wrong, 2, now) == nil {
		t.Fatal("lease crossed installation generation")
	}
	now = requested.Add(ConnectorLeaseLifetime)
	if lease.Check() == nil {
		t.Fatal("disconnected lease survived exact expiry")
	}
	if lease.Renew(binding, 2, requested) == nil {
		t.Fatal("delayed old response revived access")
	}
	if err := lease.Renew(binding, 3, now); err != nil {
		t.Fatal(err)
	}
	lease.Invalidate()
	now = now.Add(time.Second)
	if lease.Check() == nil || lease.Renew(binding, 4, now) == nil {
		t.Fatal("invalidated identity lease reactivated")
	}
}

func TestLeaseRejectsClockRollbackAndInvalidIdentity(t *testing.T) {
	for _, binding := range []LeaseBinding{{}, {Installation: "origin-a", Resource: "router", Epoch: strings.Repeat("A", 64), Generation: 1}} {
		if _, err := NewLease(binding, nil); err == nil {
			t.Fatal("invalid lease binding")
		}
	}
	now := time.Now()
	binding := LeaseBinding{Installation: "origin-a", Resource: "router", Epoch: strings.Repeat("b", 64), Generation: 1}
	lease, err := NewLease(binding, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	if err := lease.Renew(binding, 1, now); err != nil {
		t.Fatal(err)
	}
	now = now.Add(-time.Second)
	if lease.Check() == nil {
		t.Fatal("clock rollback extended lease")
	}
}
