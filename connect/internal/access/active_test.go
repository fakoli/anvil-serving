package access

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestActiveRevocationAndShutdown(t *testing.T) {
	keys, _, _, _ := keyFixture(t)
	raw, key := issue(t, keys)
	admitted, err := keys.Authenticate(raw, "router", "POST")
	if err != nil {
		t.Fatal(err)
	}
	active, err := NewActive(2, 10*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	defer active.Close()
	ctx, release, err := active.Watch(context.Background(), func() error { return keys.Check(admitted) })
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	if err := keys.Revoke(key.ID); err != nil {
		t.Fatal(err)
	}
	select {
	case <-ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("revoked admission stayed active")
	}
	if _, release, err := active.Watch(context.Background(), func() error { return keys.Check(admitted) }); err == nil {
		release()
		t.Fatal("revoked key admitted after revocation")
	}
	other, done, err := active.Watch(context.Background(), func() error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	defer done()
	var closing sync.WaitGroup
	for i := 0; i < 8; i++ {
		closing.Add(1)
		go func() {
			defer closing.Done()
			active.Close()
			if other.Err() == nil {
				t.Error("Close returned before cancelling active access")
			}
		}()
	}
	closing.Wait()
	if _, release, err := active.Watch(context.Background(), func() error { return nil }); err == nil {
		release()
		t.Fatal("closed registry admitted access")
	}
}

func TestActiveCapacityAndCancellationRace(t *testing.T) {
	active, err := NewActive(1, 10*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	defer active.Close()
	parent, cancel := context.WithCancel(context.Background())
	ctx, release, err := active.Watch(parent, func() error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if _, done, err := active.Watch(context.Background(), func() error { return nil }); err != ErrCapacity {
		if done != nil {
			done()
		}
		t.Fatal("capacity bound ignored")
	}
	cancel()
	release()
	if ctx.Err() == nil {
		t.Fatal("caller cancellation lost")
	}
	var denied atomic.Bool
	_, done, err := active.Watch(context.Background(), func() error {
		if denied.Load() {
			return ErrDenied
		}
		denied.Store(true)
		return ErrDenied
	})
	if err == nil {
		done()
		t.Fatal("authority change during admission ignored")
	}
	active.mu.Lock()
	count := len(active.items)
	active.mu.Unlock()
	if count != 0 {
		t.Fatal("denied watch leaked capacity")
	}
}

func TestActiveRegistryBounds(t *testing.T) {
	for _, maximum := range []int{0, 513} {
		if active, err := NewActive(maximum, time.Second); err == nil {
			active.Close()
			t.Fatal("unbounded registry accepted")
		}
	}
	for _, interval := range []time.Duration{time.Millisecond, 2 * time.Second} {
		if active, err := NewActive(1, interval); err == nil {
			active.Close()
			t.Fatal("unsafe poll interval accepted")
		}
	}
}
