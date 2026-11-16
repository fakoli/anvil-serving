package runtime

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/config"
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

// The existing Python installer-contract allocator supplies root-owned,
// non-writable ancestry and two disposable current-UID leaves. This opt-in
// contract exercises the shipped isolation boundary, without service/account
// changes or weakening directory policy for an ordinary temporary directory.
func TestIsolatedGatewayCrashRecoveryPreservesAuthorityAndCleanup(t *testing.T) {
	root := os.Getenv("ANVIL_CONNECT_GATEWAY_FIXTURE_ROOT")
	if root == "" || os.Getenv("ANVIL_CONNECT_WSTUNNEL") == "" {
		t.Skip("requires disposable root-owned installer fixture and pinned transport")
	}
	if filepath.Clean(root) != root || (filepath.Dir(root) != "/opt" && filepath.Dir(root) != "/usr/local") || !strings.HasPrefix(filepath.Base(root), "anvil-connect-install-test.") {
		t.Fatal("refusing a path outside the existing disposable installer fixture")
	}
	declaration := gatewaySettings(t)
	declaration.StateDirectory = filepath.Join(root, "gateway")
	declaration.Ingress = &ingresshttp.Policy{GatewayUID: uint32(os.Geteuid()), EdgeUID: uint32(os.Geteuid()) + 1, GroupID: uint32(os.Getegid()), Directory: filepath.Join(root, "ingress")}
	if err := InitializeGateway(declaration); err != nil {
		t.Fatal(err)
	}
	statePath := filepath.Join(declaration.StateDirectory, "authority")
	state, err := store.Open(statePath, nil)
	if err != nil {
		t.Fatal(err)
	}
	rules := []config.Rule{declaration.Gateway.Resources[0].Rule}
	keys, err := access.NewKeys(state, rules)
	if err != nil {
		_ = state.Close()
		t.Fatal(err)
	}
	grants := []access.Grant{{Resource: rules[0].ID, Methods: []string{"GET"}}}
	if err := keys.SetPrincipal("retained-sdk", grants, false); err != nil {
		_ = state.Close()
		t.Fatal(err)
	}
	secret, issued, err := keys.Issue("retained-sdk", grants, time.Hour)
	if err != nil {
		_ = state.Close()
		t.Fatal(err)
	}
	if err := state.Close(); err != nil {
		t.Fatal(err)
	}
	adminPath := filepath.Join(declaration.StateDirectory, "admin.sock")
	ingressPath := filepath.Join(declaration.Ingress.Directory, "ingress.sock")
	privateDirectory, err := privatefiles.OpenExisting(declaration.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer privateDirectory.Close()
	adminPin, err := privateDirectory.PinPath("admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer adminPin.Close()
	leftovers := func() map[string]os.FileInfo {
		t.Helper()
		before := map[string]os.FileInfo{}
		for _, path := range []string{adminPath, ingressPath} {
			listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
			if err != nil {
				t.Fatal(err)
			}
			listener.SetUnlinkOnClose(false)
			if err := listener.Close(); err != nil {
				t.Fatal(err)
			}
			mode := os.FileMode(0600)
			if path == ingressPath {
				mode = 0660
			}
			if err := os.Chmod(path, mode); err != nil {
				t.Fatal(err)
			}
			before[path], err = os.Lstat(path)
			if err != nil {
				t.Fatal(err)
			}
		}
		return before
	}
	assertPreserved := func(before map[string]os.FileInfo) {
		t.Helper()
		for path, original := range before {
			entries, err := filepath.Glob(filepath.Join(filepath.Dir(path), "refused-sockets-*", filepath.Base(path)))
			if err != nil {
				t.Fatal(err)
			}
			found := false
			for _, entry := range entries {
				info, err := os.Lstat(entry)
				if err == nil && os.SameFile(original, info) {
					found = true
				}
			}
			if !found {
				t.Fatal("original socket inode was not preserved")
			}
		}
	}
	before := leftovers()
	gateway, err := StartGateway(context.Background(), declaration, func(string) (string, bool) { t.Error("API-only startup requested a secret"); return "", false })
	if err != nil {
		t.Fatal("isolated crash recovery failed", err)
	}
	defer gateway.Close()
	assertPreserved(before)
	status, err := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "status"})
	if err != nil || status.Epoch != issued.Epoch {
		t.Fatal("startup changed authority epoch", err)
	}
	live := map[string]os.FileInfo{}
	for _, path := range []string{adminPath, ingressPath} {
		live[path], err = os.Lstat(path)
		if err != nil {
			t.Fatal(err)
		}
	}
	contender, err := StartGateway(context.Background(), declaration, func(string) (string, bool) { return "", false })
	if err == nil || contender != nil {
		if contender != nil {
			contender.Close()
		}
		t.Fatal("second gateway acquired authority")
	}
	for path, original := range live {
		current, err := os.Lstat(path)
		if err != nil || !os.SameFile(original, current) {
			t.Fatal("contender displaced live socket", err)
		}
	}
	status, err = admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "status"})
	if err != nil || status.Epoch != issued.Epoch {
		t.Fatal("contender disturbed live authority", err)
	}
	gateway.Close()

	// Secret lookup is reached after ingress binding but before transport
	// startup. Hold it with channels so another store opener observes cleanup.
	before = leftovers()
	failed := browserLifetimeGateway(t)
	failed.StateDirectory, failed.Ingress = declaration.StateDirectory, declaration.Ingress
	entered, release := make(chan struct{}), make(chan struct{})
	var releaseOnce sync.Once
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	finished := make(chan struct{})
	result := make(chan error, 1)
	t.Cleanup(func() {
		unblock()
		select {
		case <-finished:
		case <-time.After(5 * time.Second):
			t.Error("failed-start worker did not stop")
		}
	})
	go func() {
		defer close(finished)
		g, err := StartGateway(context.Background(), failed, func(string) (string, bool) { close(entered); <-release; return "", false })
		if g != nil {
			g.Close()
		}
		result <- err
	}()
	select {
	case <-entered:
	case err := <-result:
		t.Fatal("failure never reached bound ingress", err)
	case <-time.After(5 * time.Second):
		t.Fatal("startup did not reach failure barrier")
	}
	if _, err := os.Lstat(ingressPath); err != nil {
		unblock()
		<-result
		t.Fatal("failure barrier preceded ingress bind", err)
	}
	opened := make(chan *store.Store, 1)
	openErrors := make(chan error, 1)
	go func() {
		next, err := store.Open(statePath, nil)
		if err != nil {
			openErrors <- err
		} else {
			opened <- next
		}
	}()
	unblock()
	select {
	case state = <-opened:
	case err := <-openErrors:
		t.Fatal("failed startup leaked store ownership", err)
	case <-time.After(5 * time.Second):
		t.Fatal("store ownership was not released")
	}
	defer state.Close()
	for _, path := range []string{adminPath, ingressPath} {
		if _, err := os.Lstat(path); !os.IsNotExist(err) {
			t.Fatal("listener survived release of authority ownership", err)
		}
	}
	if err := <-result; err == nil {
		t.Fatal("missing secret unexpectedly started gateway")
	}
	assertPreserved(before)
	keys, err = access.NewKeys(state, rules)
	if err != nil {
		t.Fatal(err)
	}
	admitted, err := keys.Authenticate(secret, rules[0].ID, "GET")
	if err != nil || admitted.Principal != issued.Principal {
		t.Fatal("recovery changed retained grant", err)
	}
	if _, err := keys.Authenticate(secret, rules[0].ID, "POST"); err == nil {
		t.Fatal("recovery widened retained grant")
	}
}
