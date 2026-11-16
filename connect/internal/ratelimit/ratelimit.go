// Package ratelimit provides keyed token buckets with bounded memory for
// anonymous Connect endpoints. Callers serialize access.
package ratelimit

import "time"

type bucket struct {
	tokens float64
	last   time.Time
}

// Limiter is a keyed token bucket. When the table is full the least recently
// used key is forgotten; that key returns with a fresh allowance, so callers
// that need a hard total pair it with a single-key global Limiter.
type Limiter struct {
	burst   float64
	refill  time.Duration
	max     int
	buckets map[string]bucket
}

// New returns a Limiter of burst tokens per key, refilling one token every
// refill, tracking at most max keys.
func New(burst int, refill time.Duration, max int) *Limiter {
	return &Limiter{burst: float64(burst), refill: refill, max: max, buckets: map[string]bucket{}}
}

// Key names one bucket in one Limiter.
type Key struct {
	Limiter *Limiter
	Key     string
}

// Take consumes one token from every bucket only when each holds a whole
// token, so a refusal by one limiter spends nothing from the others.
func Take(now time.Time, keys ...Key) bool {
	levels := make([]bucket, len(keys))
	for i, k := range keys {
		current, known := k.Limiter.buckets[k.Key]
		if !known {
			current = bucket{tokens: k.Limiter.burst, last: now}
		}
		if levels[i] = k.Limiter.refilled(current, now); levels[i].tokens < 1 {
			return false
		}
	}
	for i, k := range keys {
		levels[i].tokens--
		k.Limiter.store(k.Key, levels[i])
	}
	return true
}

// Len reports how many keys are tracked.
func (l *Limiter) Len() int { return len(l.buckets) }

// Tracked reports whether key currently has a bucket.
func (l *Limiter) Tracked(key string) bool { _, ok := l.buckets[key]; return ok }

func (l *Limiter) store(key string, b bucket) {
	if _, known := l.buckets[key]; !known && len(l.buckets) >= l.max {
		// ponytail: O(max) scan per new key on a full table; use a list-backed
		// LRU if a table grows well beyond a few thousand keys.
		var oldest string
		for id, other := range l.buckets {
			if oldest == "" || other.last.Before(l.buckets[oldest].last) {
				oldest = id
			}
		}
		delete(l.buckets, oldest)
	}
	l.buckets[key] = b
}

func (l *Limiter) refilled(b bucket, now time.Time) bucket {
	if now.After(b.last) {
		b.tokens += float64(now.Sub(b.last)) / float64(l.refill)
		if b.tokens > l.burst {
			b.tokens = l.burst
		}
		b.last = now
	}
	return b
}
