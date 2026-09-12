package testpki

import (
	"context"
	"net"
	"sync"
)

// PipeListener exercises real TLS and HTTP without requiring sandbox sockets.
// It provides no evidence about OS binding, DNS, proxies or child processes.
type PipeListener struct {
	pending chan net.Conn
	done    chan struct{}
	once    sync.Once
}

func NewPipeListener() *PipeListener {
	return &PipeListener{pending: make(chan net.Conn), done: make(chan struct{})}
}
func (l *PipeListener) Addr() net.Addr { return &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 18443} }
func (l *PipeListener) Close() error   { l.once.Do(func() { close(l.done) }); return nil }
func (l *PipeListener) Accept() (net.Conn, error) {
	select {
	case c := <-l.pending:
		return c, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}
func (l *PipeListener) DialContext(ctx context.Context, _, _ string) (net.Conn, error) {
	a, b := net.Pipe()
	select {
	case l.pending <- a:
		return b, nil
	case <-ctx.Done():
		a.Close()
		b.Close()
		return nil, ctx.Err()
	case <-l.done:
		a.Close()
		b.Close()
		return nil, net.ErrClosed
	}
}
