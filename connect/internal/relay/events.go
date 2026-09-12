package relay

import (
	"log"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
)

// Events retains only bounded metadata. It has no error/output/body input and
// emits rate-limited codes through the existing daemon log surface. Snapshots
// are native-only; this does not change the closed admin wire response.
type Events struct {
	mu     sync.Mutex
	items  []Event
	logged map[string]time.Time
}

type Event struct {
	At                     time.Time
	Path, Resource, Reason string
}

func (e *Events) Record(path, resource, reason string) {
	if e == nil || (path != "local" && path != "public") || (resource != "" && !config.ValidID(resource)) {
		return
	}
	switch reason {
	case "entry_bind_failed", "entry_tls_failed", "entry_stopped", "entry_listening", "entry_capacity_exhausted",
		"tunnel_established", "tunnel_establishment_failed", "tunnel_disconnected",
		"renewal_failed", "renewal_resumed", "lease_expired", "authority_denied":
	default:
		return
	}
	e.mu.Lock()
	now := time.Now()
	if e.logged == nil {
		e.logged = make(map[string]time.Time)
	}
	// The key space is the closed reason/path vocabulary, not user input or
	// resource cardinality. Repeated hostile handshakes cannot flood stderr.
	key := path + ":" + reason
	emit := now.Sub(e.logged[key]) >= time.Second
	if emit {
		e.logged[key] = now
	}
	if len(e.items) == 64 {
		copy(e.items, e.items[1:])
		e.items = e.items[:63]
	}
	e.items = append(e.items, Event{now, path, resource, reason})
	e.mu.Unlock()
	if emit {
		log.Printf("connect_event path=%s resource=%s reason=%s", path, resource, reason)
	}
}

func (e *Events) Snapshot() []Event {
	if e == nil {
		return nil
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	return append([]Event(nil), e.items...)
}
