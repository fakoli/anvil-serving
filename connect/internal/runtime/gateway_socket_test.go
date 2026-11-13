package runtime

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/ingresshttp"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

func TestGatewayRecoveryRequiresExclusiveStoreAndBothSocketPreflights(t *testing.T) {
	for _, held := range []bool{true, false} {
		name := "public-preflight-refuses"
		if held {
			name = "another-gateway-holds-store"
		}
		t.Run(name, func(t *testing.T) {
			declaration := gatewaySettings(t)
			if err := InitializeGateway(declaration); err != nil {
				t.Fatal(err)
			}
			declaration.Ingress = &ingresshttp.Policy{GatewayUID: uint32(os.Geteuid()), EdgeUID: uint32(os.Geteuid()) + 1, GroupID: uint32(os.Getegid()), Directory: filepath.Join(t.TempDir(), "ingress")}
			if declaration.Validate() != nil {
				t.Fatal("invalid synthetic declaration")
			}
			directory, err := privatefiles.OpenExisting(declaration.StateDirectory)
			if err != nil {
				t.Fatal(err)
			}
			defer directory.Close()
			pin, err := directory.PinPath("admin.sock")
			if err != nil {
				t.Fatal(err)
			}
			defer pin.Close()
			listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: pin.Path(), Net: "unix"})
			if err != nil {
				t.Fatal(err)
			}
			listener.SetUnlinkOnClose(false)
			_ = listener.Close()
			if err := os.Chmod(pin.Path(), 0600); err != nil {
				t.Fatal(err)
			}
			before, err := os.Lstat(pin.Path())
			if err != nil {
				t.Fatal(err)
			}
			if held {
				owner, err := store.Open(filepath.Join(declaration.StateDirectory, "authority"), nil)
				if err != nil {
					t.Fatal(err)
				}
				defer owner.Close()
			}
			// The internal composition seam supplies no prepared public ingress.
			// It must fail either on exclusive ownership or the second preflight,
			// before quarantining the otherwise recoverable administrative socket.
			gateway, err := composeGateway(context.Background(), declaration, func(string) (string, bool) { return "", false }, nil)
			if err == nil || gateway != nil {
				t.Fatal("unsafe startup succeeded")
			}
			after, err := os.Lstat(pin.Path())
			if err != nil || !os.SameFile(before, after) {
				t.Fatal("failed startup displaced prior socket", err)
			}
			entries, err := filepath.Glob(filepath.Join(declaration.StateDirectory, "refused-sockets-*"))
			if err != nil || len(entries) != 0 {
				t.Fatal("failed admission created recovery files", entries, err)
			}
			if !held {
				owner, err := store.Open(filepath.Join(declaration.StateDirectory, "authority"), nil)
				if err != nil {
					t.Fatal("failed startup leaked exclusive ownership", err)
				}
				_ = owner.Close()
			}
		})
	}
}
