package transport

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"net"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
)

// LocalBinding separates the literal destination, TLS identity and HTTP Host.
// It has no proxy, resolver, redirect or insecure-TLS escape hatch.
type LocalBinding struct{ Address, ServerName, Host string }

func (l LocalBinding) Validate() error {
	if !config.LoopbackAddress(l.Address) || !config.ValidHost(l.ServerName) || !config.ValidHost(l.Host) {
		return ErrProcess
	}
	return nil
}

// VerifyLocalEntry verifies the outer TLS entry within one total deadline. It
// is NOT evidence of an admitted reverse registration, nor a replacement for
// executing and qualifying the pinned wstunnel adapter.
func VerifyLocalEntry(ctx context.Context, l LocalBinding, roots *x509.CertPool) error {
	if ctx == nil || l.Validate() != nil || roots == nil {
		return ErrProcess
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	conn, err := (&net.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "tcp4", l.Address)
	if err != nil {
		return ErrProcess
	}
	defer conn.Close()
	client := tls.Client(conn, &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13, ServerName: l.ServerName, RootCAs: roots.Clone(), NextProtos: []string{"http/1.1"}, VerifyConnection: func(s tls.ConnectionState) error {
		if len(s.VerifiedChains) == 0 || len(s.PeerCertificates) == 0 || !relay.ExactPeerName(s.PeerCertificates[0], l.ServerName) || s.NegotiatedProtocol != "http/1.1" {
			return ErrProcess
		}
		return nil
	}})
	if client.HandshakeContext(ctx) != nil {
		return ErrProcess
	}
	return nil
}
