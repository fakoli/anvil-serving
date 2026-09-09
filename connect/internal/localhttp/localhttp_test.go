package localhttp

import (
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"golang.org/x/sys/unix"
)

func pinnedPath(t *testing.T, directory *privatefiles.Directory, name string) string {
	t.Helper()
	pinned, err := directory.PinPath(name)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = pinned.Close() })
	return pinned.Path()
}

func testDirectory(t *testing.T) *privatefiles.Directory {
	t.Helper()
	directory, err := privatefiles.Open(filepath.Join(t.TempDir(), "private"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = directory.Close() })
	return directory
}

func TestListenRoundTripOwnerOnlyAndCleanup(t *testing.T) {
	directory := testDirectory(t)
	listener, err := Listen(directory, "admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	path := pinnedPath(t, directory, "admin.sock")
	var stat unix.Stat_t
	if err := unix.Lstat(path, &stat); err != nil || stat.Mode&07777 != 0600 || stat.Mode&unix.S_IFMT != unix.S_IFSOCK {
		t.Fatalf("socket mode = %#o, error = %v", stat.Mode, err)
	}
	accepted := make(chan error, 1)
	go func() {
		connection, err := listener.Accept()
		if err != nil {
			accepted <- err
			return
		}
		defer connection.Close()
		data, err := io.ReadAll(io.LimitReader(connection, 32))
		if err == nil && string(data) != "request" {
			err = errors.New("wrong request")
		}
		if err == nil {
			_, err = connection.Write([]byte("response"))
		}
		accepted <- err
	}()
	client, err := net.DialTimeout("unix", path, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Write([]byte("request")); err != nil {
		t.Fatal(err)
	}
	if unix, ok := client.(*net.UnixConn); ok {
		_ = unix.CloseWrite()
	}
	result, err := io.ReadAll(client)
	if err != nil || string(result) != "response" {
		t.Fatalf("response = %q, %v", result, err)
	}
	_ = client.Close()
	if err := <-accepted; err != nil {
		t.Fatal(err)
	}
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("socket remained after Close: %v", err)
	}
}

func TestListenRefusesExistingEntries(t *testing.T) {
	directory := testDirectory(t)
	for _, name := range []string{"regular.sock", "link.sock", "busy.sock"} {
		t.Run(name, func(t *testing.T) {
			path := pinnedPath(t, directory, name)
			var cleanup func()
			switch name {
			case "regular.sock":
				if err := os.WriteFile(path, []byte("existing"), 0600); err != nil {
					t.Fatal(err)
				}
				cleanup = func() { _ = os.Remove(path) }
			case "link.sock":
				if err := os.Symlink("regular.sock", path); err != nil {
					t.Fatal(err)
				}
				cleanup = func() { _ = os.Remove(path) }
			case "busy.sock":
				busy, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
				if err != nil {
					t.Fatal(err)
				}
				busy.SetUnlinkOnClose(false)
				cleanup = func() { _ = busy.Close(); _ = os.Remove(path) }
			}
			defer cleanup()
			if listener, err := Listen(directory, name); err == nil {
				_ = listener.Close()
				t.Fatal("Listen accepted an existing entry")
			}
			if _, err := os.Lstat(path); err != nil {
				t.Fatalf("existing entry was removed: %v", err)
			}
		})
	}
}

func TestCloseDoesNotUnlinkReplacedSocketPath(t *testing.T) {
	directory := testDirectory(t)
	listener, err := Listen(directory, "admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	path := pinnedPath(t, directory, "admin.sock")
	moved := path + ".moved"
	if err := os.Rename(path, moved); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	contents, err := os.ReadFile(path)
	if err != nil || string(contents) != "replacement" {
		t.Fatalf("replacement path was removed: %q, %v", contents, err)
	}
	_ = os.Remove(moved)
}

func TestListenerSurvivesOriginDirectoryClose(t *testing.T) {
	directory := testDirectory(t)
	listener, err := Listen(directory, "admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	path := pinnedPath(t, directory, "admin.sock")
	if err := directory.Close(); err != nil {
		t.Fatal(err)
	}
	accepted := make(chan error, 1)
	go func() {
		connection, err := listener.Accept()
		if err == nil {
			_ = connection.Close()
		}
		accepted <- err
	}()
	connection, err := net.DialTimeout("unix", path, time.Second)
	if err != nil {
		t.Fatalf("listener changed after original Directory close: %v", err)
	}
	_ = connection.Close()
	if err := <-accepted; err != nil {
		t.Fatal(err)
	}
}
