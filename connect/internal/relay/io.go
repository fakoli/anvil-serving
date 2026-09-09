// Package relay supplies bounded HTTP streaming primitives shared by the gateway
// and origin adapter. Resource policy and authentication stay with their callers.
package relay

import (
	"bufio"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
)

// WrapConn applies a fresh idle deadline to each network read and write.
func WrapConn(conn net.Conn, idle time.Duration) net.Conn { return &Conn{Conn: conn, idle: idle} }

// Writer preserves streaming and upgrades while binding them to request lifetime.
func Writer(w http.ResponseWriter, ctx context.Context, idle time.Duration) http.ResponseWriter {
	return &responseWriter{ResponseWriter: w, ctx: ctx, idle: idle}
}

// Body bounds stalled uploads as well as downstream writes. Cancelling an
// outbound RoundTrip alone cannot interrupt a copier blocked on the caller's
// body. The server read deadline interrupts that read before Close can drain it.
func Body(w http.ResponseWriter, body io.ReadCloser, ctx context.Context, idle time.Duration) io.ReadCloser {
	b := &requestBody{body: body, control: http.NewResponseController(w), idle: idle}
	b.stop = context.AfterFunc(ctx, b.abort)
	return b
}

type requestBody struct {
	body    io.ReadCloser
	control *http.ResponseController
	idle    time.Duration
	once    sync.Once
	mu      sync.Mutex
	closed  bool
	eof     bool
	stop    func() bool
}

func (b *requestBody) Read(p []byte) (int, error) {
	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return 0, io.ErrClosedPipe
	}
	if err := b.control.SetReadDeadline(time.Now().Add(b.idle)); err != nil && !errors.Is(err, http.ErrNotSupported) {
		b.mu.Unlock()
		return 0, err
	}
	b.mu.Unlock()
	n, err := b.body.Read(p)
	if err == io.EOF {
		b.mu.Lock()
		b.eof = true
		if !b.closed {
			_ = b.control.SetReadDeadline(time.Time{})
		}
		b.mu.Unlock()
	}
	return n, err
}
func (b *requestBody) abort() {
	b.mu.Lock()
	b.closed = true
	_ = b.control.SetReadDeadline(time.Now())
	b.mu.Unlock()
	b.once.Do(func() { _ = b.body.Close() })
}
func (b *requestBody) Close() error {
	b.stop()
	b.mu.Lock()
	complete := b.eof
	if complete {
		b.closed = true
	}
	b.mu.Unlock()
	if !complete {
		b.abort()
	} else {
		b.once.Do(func() { _ = b.body.Close() })
	}
	return nil
}

type bufferPool struct {
	buffers chan []byte
	size    int
}

func NewBufferPool(limits config.Limits) *bufferPool {
	return &bufferPool{buffers: make(chan []byte, limits.Concurrent), size: min(limits.BufferBytes, 32*1024)}
}
func (p *bufferPool) Get() []byte {
	select {
	case buffer := <-p.buffers:
		return buffer
	default:
		return make([]byte, p.size)
	}
}
func (p *bufferPool) Put(buffer []byte) {
	if cap(buffer) != p.size {
		return
	}
	select {
	case p.buffers <- buffer[:p.size]:
	default:
	}
}

type Conn struct {
	net.Conn
	idle time.Duration
}

func (c *Conn) Read(buffer []byte) (int, error) {
	if err := c.Conn.SetReadDeadline(time.Now().Add(c.idle)); err != nil {
		return 0, err
	}
	return c.Conn.Read(buffer)
}
func (c *Conn) Write(buffer []byte) (int, error) {
	if err := c.Conn.SetWriteDeadline(time.Now().Add(c.idle)); err != nil {
		return 0, err
	}
	return c.Conn.Write(buffer)
}

type responseWriter struct {
	http.ResponseWriter
	idle time.Duration
	ctx  context.Context
}

func (w *responseWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }
func (w *responseWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	conn, buffered, err := http.NewResponseController(w.ResponseWriter).Hijack()
	if err != nil {
		return nil, nil, err
	}
	wrapped := &Conn{Conn: conn, idle: w.idle}
	if err := conn.SetWriteDeadline(time.Now().Add(w.idle)); err != nil {
		conn.Close()
		return nil, nil, err
	}
	// ReverseProxy closes its upstream on cancellation. Also close the caller
	// here so a stalled downstream WebSocket write cannot prevent teardown.
	context.AfterFunc(w.ctx, func() { wrapped.Close() })
	return wrapped, buffered, nil
}
func (w *responseWriter) Write(buffer []byte) (int, error) {
	if err := http.NewResponseController(w.ResponseWriter).SetWriteDeadline(time.Now().Add(w.idle)); err != nil && !errors.Is(err, http.ErrNotSupported) {
		return 0, err
	}
	return w.ResponseWriter.Write(buffer)
}
func (w *responseWriter) FlushError() error {
	controller := http.NewResponseController(w.ResponseWriter)
	if err := controller.SetWriteDeadline(time.Now().Add(w.idle)); err != nil && !errors.Is(err, http.ErrNotSupported) {
		return err
	}
	return controller.Flush()
}
