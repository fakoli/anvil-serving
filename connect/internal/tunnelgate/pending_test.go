package tunnelgate

import (
	"net"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/relay"
)

type observedDeadlineConn struct {
	net.Conn
	reads, writes chan time.Time
}

func (c *observedDeadlineConn) SetReadDeadline(deadline time.Time) error {
	c.reads <- deadline
	return c.Conn.SetReadDeadline(deadline)
}

func (c *observedDeadlineConn) SetWriteDeadline(deadline time.Time) error {
	c.writes <- deadline
	return c.Conn.SetWriteDeadline(deadline)
}

func TestPendingReadBecomesBoundedAtUpgrade(t *testing.T) {
	left, right := net.Pipe()
	defer left.Close()
	defer right.Close()
	raw := &observedDeadlineConn{Conn: left, reads: make(chan time.Time, 4), writes: make(chan time.Time, 1)}
	conn := &pendingTunnelConn{Conn: relay.WrapConn(raw, 45*time.Second), raw: raw}
	read := make(chan error, 1)
	go func() { _, err := conn.Read(make([]byte, 1)); read <- err }()
	if deadline := <-raw.reads; !deadline.IsZero() {
		t.Fatal("pending demand received an idle deadline")
	}
	if err := conn.completeUpgrade(); err != nil {
		t.Fatal(err)
	}
	assertIdleDeadline := func(deadline time.Time) {
		t.Helper()
		if remaining := time.Until(deadline); remaining < 44*time.Second || remaining > 45*time.Second {
			t.Fatal("upgrade failed to arm the existing idle bound", remaining)
		}
	}
	assertIdleDeadline(<-raw.reads)
	if _, err := right.Write([]byte{1}); err != nil {
		t.Fatal(err)
	}
	if err := <-read; err != nil {
		t.Fatal(err)
	}
	go func() { _, err := conn.Read(make([]byte, 1)); read <- err }()
	assertIdleDeadline(<-raw.reads)
	go func() { _, err := conn.Write([]byte{1}); read <- err }()
	assertIdleDeadline(<-raw.writes)
	right.Close()
	<-read
	<-read
}
