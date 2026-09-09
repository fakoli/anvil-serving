package main

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
	"github.com/fakoli/anvil-serving/connect/internal/config"
)

func portableClientRule() config.Rule {
	return config.Rule{
		ID: "router", Host: "router.example.test", PathPrefix: "/v1",
		Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer",
		Limits: config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 3},
	}
}

// This runs the same process path built for macOS: it binds only the declared
// IPv4 loopback address, consumes the local key by explicit env reference, and
// releases the listener when cancelled. Remote gateway qualification belongs
// to the separately provisioned deployment test.
func TestPortableClientProcessCancelsAndReleasesLoopback(t *testing.T) {
	probe, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := probe.Addr().String()
	if err := probe.Close(); err != nil {
		t.Fatal(err)
	}
	key, err := client.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	declaration := clientconfig.Config{
		Schema: "anvil-connect.client-runtime/v1", Rule: portableClientRule(), Listen: address,
		LocalKeyEnv: "TEST_LOCAL_KEY", RemoteKeyEnv: "TEST_REMOTE_KEY",
	}
	ctx, cancel := context.WithCancel(context.Background())
	started := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		done <- serveClient(ctx, declaration, func(name string) (string, bool) {
			return key, name == "TEST_LOCAL_KEY"
		}, func() error {
			close(started)
			return nil
		})
	}()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("client did not bind its declared loopback listener")
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("client did not stop after cancellation")
	}
	rebound, err := net.Listen("tcp4", address)
	if err != nil {
		t.Fatal("client listener leaked", err)
	}
	_ = rebound.Close()
}
