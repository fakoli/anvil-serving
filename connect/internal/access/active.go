package access

import (
	"context"
	"errors"
	"math"
	"sync"
	"time"
)

var ErrCapacity = errors.New("active access capacity unavailable")

// Active ties a bounded set of connections to current local authority. Checks
// must be short local reads, never network calls. A separate transport context
// controls maximum duration and body/read/write deadlines.
type Active struct {
	mu        sync.Mutex
	items     map[uint64]activeEntry
	maximum   int
	next      uint64
	closed    bool
	closeOnce sync.Once
	stop      chan struct{}
	done      chan struct{}
}

type activeEntry struct {
	check  func() error
	cancel context.CancelFunc
}

func NewActive(maximum int, interval time.Duration) (*Active, error) {
	if maximum < 1 || maximum > 512 || interval < 10*time.Millisecond || interval > time.Second {
		return nil, ErrCapacity
	}
	a := &Active{items: map[uint64]activeEntry{}, maximum: maximum, stop: make(chan struct{}), done: make(chan struct{})}
	go a.run(interval)
	return a, nil
}

func (a *Active) run(interval time.Duration) {
	defer close(a.done)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-a.stop:
			return
		case <-ticker.C:
		}
		a.mu.Lock()
		snapshot := make(map[uint64]activeEntry, len(a.items))
		for id, item := range a.items {
			snapshot[id] = item
		}
		a.mu.Unlock()
		for id, item := range snapshot {
			select {
			case <-a.stop:
				return
			default:
			}
			if item.check() != nil {
				a.remove(id)
			}
		}
	}
}

func (a *Active) remove(id uint64) {
	a.mu.Lock()
	item, exists := a.items[id]
	delete(a.items, id)
	a.mu.Unlock()
	if exists {
		item.cancel()
	}
}

// Watch registers cancellation before the final authority check. Revocation
// racing that check therefore sees either a denied admission or a tracked stream.
// Release must be called when streaming/upgrade handling has actually ended.
func (a *Active) Watch(parent context.Context, check func() error) (context.Context, func(), error) {
	if parent == nil || parent.Err() != nil || check == nil {
		return nil, nil, ErrDenied
	}
	ctx, cancel := context.WithCancel(parent)
	a.mu.Lock()
	if a.closed || len(a.items) >= a.maximum || a.next == math.MaxUint64 {
		a.mu.Unlock()
		cancel()
		return nil, nil, ErrCapacity
	}
	a.next++
	id := a.next
	a.items[id] = activeEntry{check, cancel}
	a.mu.Unlock()
	stop := context.AfterFunc(ctx, func() { a.remove(id) })
	release := sync.OnceFunc(func() { stop(); a.remove(id); cancel() })
	if check() != nil || ctx.Err() != nil {
		release()
		return nil, nil, ErrDenied
	}
	return ctx, release, nil
}

// Close denies new admissions, cancels active access, and joins the poller.
func (a *Active) Close() {
	a.closeOnce.Do(func() {
		a.mu.Lock()
		a.closed = true
		close(a.stop)
		items := a.items
		a.items = map[uint64]activeEntry{}
		a.mu.Unlock()
		for _, item := range items {
			item.cancel()
		}
	})
	<-a.done
}
