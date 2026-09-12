package relay

import (
	"bufio"
	"context"
	"net"
	"net/http"
	"testing"
	"time"
)

type hijackWriter struct{ conn net.Conn }

func (w hijackWriter) Header() http.Header         { return http.Header{} }
func (w hijackWriter) WriteHeader(int)             {}
func (w hijackWriter) Write(p []byte) (int, error) { return w.conn.Write(p) }
func (w hijackWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	return w.conn, bufio.NewReadWriter(bufio.NewReader(w.conn), bufio.NewWriter(w.conn)), nil
}
func TestEntryRelayCancellationAndBoundedCredentialFreeEvents(t *testing.T) {
	a, b := net.Pipe()
	defer a.Close()
	defer b.Close()
	ctx, cancel := context.WithCancel(context.Background())
	conn, _, err := http.NewResponseController(Writer(hijackWriter{a}, ctx, time.Second)).Hijack()
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	cancel()
	b.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := b.Read(make([]byte, 1)); err == nil {
		t.Fatal("hijack survived cancellation")
	} else if e, ok := err.(net.Error); ok && e.Timeout() {
		t.Fatal("cancellation waited for idle deadline")
	}
	var events Events
	for n := 0; n < 100; n++ {
		events.Record("local", "router", "tunnel_disconnected")
	}
	events.Record("local", "router", "Authorization: Bearer synthetic")
	events.Record("https://private.example.test", "router", "renewal_failed")
	events.Record("local", "bad\nresource", "renewal_failed")
	snapshot := events.Snapshot()
	if len(snapshot) != 64 {
		t.Fatal("event bounds changed", len(snapshot))
	}
	snapshot[0].Reason = "mutated"
	if events.Snapshot()[0].Reason != "tunnel_disconnected" {
		t.Fatal("snapshot aliases state")
	}
}
