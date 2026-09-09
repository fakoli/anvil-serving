package main

import (
	"context"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
)

// serveClient owns only the declared IPv4 loopback listener. The forwarder
// fixes the public destination and obtains credentials only through the two
// declared environment references.
func serveClient(ctx context.Context, c clientconfig.Config, lookup func(string) (string, bool), started func() error) error {
	key, ok := lookup(c.LocalKeyEnv)
	if !ok {
		return client.ErrConfiguration
	}
	forwarder, err := client.New(c.Rule, c.Listen, key, c.RemoteKeyEnv, lookup, client.Options{})
	if err != nil {
		return err
	}
	defer forwarder.Close()
	listener, err := net.Listen("tcp", c.Listen)
	if err != nil {
		return err
	}
	defer listener.Close()
	server := &http.Server{Handler: forwarder, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 65536, ErrorLog: log.New(io.Discard, "", 0), BaseContext: func(net.Listener) context.Context { return ctx }}
	defer server.Close()
	exited := make(chan error, 1)
	go func() { exited <- server.Serve(listener) }()
	// Every return after Serve starts closes and reaps this owned server.
	defer func() { _ = server.Close(); <-exited }()
	if err := started(); err != nil {
		return err
	}
	select {
	case <-ctx.Done():
		return nil
	case err := <-exited:
		exited <- err
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
}
