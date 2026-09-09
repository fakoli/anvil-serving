package access

import (
	"encoding/hex"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
)

const ConnectorLeaseLifetime = 45 * time.Second

type LeaseBinding struct {
	Installation string
	Resource     string
	Epoch        string
	Generation   uint64
}

// Lease is local, volatile permission at an origin connector. A restart starts
// closed. Renewal is allowed only after the control client authenticates a fresh
// gateway response for this exact installation/resource/epoch/generation.
type Lease struct {
	mu          sync.Mutex
	binding     LeaseBinding
	now         func() time.Time
	sequence    uint64
	requestedAt time.Time
	expiresAt   time.Time
	invalid     bool
}

func NewLease(binding LeaseBinding, now func() time.Time) (*Lease, error) {
	epoch, err := hex.DecodeString(binding.Epoch)
	if !config.ValidID(binding.Installation) || !config.ValidID(binding.Resource) || binding.Generation == 0 || err != nil || len(epoch) != 32 || hex.EncodeToString(epoch) != binding.Epoch {
		return nil, ErrDenied
	}
	if now == nil {
		now = time.Now
	}
	return &Lease{binding: binding, now: now}, nil
}

func (l *Lease) Binding() LeaseBinding { return l.binding }

// Renew uses the locally captured request-start time, never a remote timestamp.
// Its monotonic component ensures a delayed control response cannot extend the
// disconnected grace beyond 45 seconds from the request that authorized it.
// The control client owns increasing sequence numbers and response authentication.
func (l *Lease) Renew(binding LeaseBinding, sequence uint64, requestedAt time.Time) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	if l.invalid || binding != l.binding || sequence <= l.sequence || requestedAt.IsZero() || now.Before(requestedAt) || !now.Before(requestedAt.Add(ConnectorLeaseLifetime)) || (!l.requestedAt.IsZero() && !requestedAt.After(l.requestedAt)) {
		return ErrDenied
	}
	l.sequence, l.requestedAt, l.expiresAt = sequence, requestedAt, requestedAt.Add(ConnectorLeaseLifetime)
	return nil
}

func (l *Lease) Check() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	if l.invalid || l.sequence == 0 || now.Before(l.requestedAt) || !now.Before(l.expiresAt) {
		return ErrDenied
	}
	return nil
}

// Invalidate is terminal for this lease instance. Rotation/recovery constructs
// a new instance only after the new identity has been approved and authenticated.
func (l *Lease) Invalidate() { l.mu.Lock(); l.invalid = true; l.mu.Unlock() }
