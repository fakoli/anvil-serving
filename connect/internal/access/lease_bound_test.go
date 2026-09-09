package access

import (
	"strings"
	"testing"
	"time"
)

func TestShortAuthenticatedLeaseUsesRequestStart(t *testing.T) {
	now := time.Now()
	binding := LeaseBinding{Installation: "origin-a", Resource: "router", Epoch: strings.Repeat("a", 64), Generation: 1}
	lease, _ := NewLease(binding, func() time.Time { return now })
	started := now
	now = now.Add(time.Second)
	if err := lease.RenewFor(binding, 1, started, 3*time.Second); err != nil {
		t.Fatal(err)
	}
	now = started.Add(3 * time.Second)
	if lease.Check() == nil {
		t.Fatal("response delay extended old generation permission")
	}
	for _, duration := range []time.Duration{-1, 0, ConnectorLeaseLifetime + 1} {
		if lease.RenewFor(binding, 2, now, duration) == nil {
			t.Fatal("invalid permission duration accepted")
		}
	}
}
