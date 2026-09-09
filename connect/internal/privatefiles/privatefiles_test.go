package privatefiles

import (
	"bytes"
	"errors"
	"net"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func openTestDirectory(t *testing.T) (*Directory, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "state")
	directory, err := Open(path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { _ = directory.Close() })
	return directory, path
}

func TestOpenRejectsUnsafeDirectory(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "target")
	if err := os.Mkdir(target, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(filepath.Join(root, "link")); !errors.Is(err, ErrPrivate) {
		t.Fatalf("Open symlink error = %v, want ErrPrivate", err)
	}
	unsafe := filepath.Join(root, "unsafe")
	if err := os.Mkdir(unsafe, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(unsafe, 0755); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(unsafe); !errors.Is(err, ErrPrivate) {
		t.Fatalf("Open mode error = %v, want ErrPrivate", err)
	}
}

func TestPrivateFileRejectsUnsafeEntries(t *testing.T) {
	directory, path := openTestDirectory(t)
	if err := directory.Create("safe", []byte("safe")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("safe", filepath.Join(path, "link")); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(filepath.Join(path, "safe"), filepath.Join(path, "hard")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(path, "mode"), []byte("mode"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Join(path, "mode"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(path, "directory"), 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"link", "hard", "mode", "directory"} {
		t.Run(name, func(t *testing.T) {
			if _, err := directory.Read(name, maxPayload); !errors.Is(err, ErrPrivate) {
				t.Fatalf("Read error = %v, want ErrPrivate", err)
			}
			if err := directory.Replace(name, []byte("replacement")); !errors.Is(err, ErrPrivate) {
				t.Fatalf("Replace error = %v, want ErrPrivate", err)
			}
		})
	}
	for _, name := range []string{"", ".", "..", "sub/file", "sub\\file", "spaces are forbidden"} {
		if err := directory.Create(name, nil); !errors.Is(err, ErrPrivate) {
			t.Fatalf("Create(%q) error = %v, want ErrPrivate", name, err)
		}
	}
}

func TestCreateIsExclusiveAndCloseRejectsOperations(t *testing.T) {
	directory, _ := openTestDirectory(t)
	if err := directory.Create("credential", []byte("first")); err != nil {
		t.Fatal(err)
	}
	if err := directory.Create("credential", []byte("second")); !errors.Is(err, ErrPrivate) {
		t.Fatalf("second Create error = %v, want ErrPrivate", err)
	}
	contents, err := directory.Read("credential", 64)
	if err != nil || string(contents) != "first" {
		t.Fatalf("Read = %q, %v", contents, err)
	}
	if err := directory.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := directory.Read("credential", 64); !errors.Is(err, ErrClosed) {
		t.Fatalf("Read after Close error = %v, want ErrClosed", err)
	}
	if err := directory.Create("other", nil); !errors.Is(err, ErrClosed) {
		t.Fatalf("Create after Close error = %v, want ErrClosed", err)
	}
	if err := directory.Replace("credential", nil); !errors.Is(err, ErrClosed) {
		t.Fatalf("Replace after Close error = %v, want ErrClosed", err)
	}
}

func TestReplaceIsAtomicForReaders(t *testing.T) {
	directory, path := openTestDirectory(t)
	old := bytes.Repeat([]byte("old"), 4096)
	one := bytes.Repeat([]byte("one"), 8192)
	two := bytes.Repeat([]byte("two"), 8192)
	if err := directory.Create("header", old); err != nil {
		t.Fatal(err)
	}
	valid := func(value []byte) bool {
		return bytes.Equal(value, old) || bytes.Equal(value, one) || bytes.Equal(value, two)
	}
	var writers sync.WaitGroup
	writers.Add(1)
	writeErr := make(chan error, 1)
	go func() {
		defer writers.Done()
		for i := 0; i < 200; i++ {
			value := one
			if i%2 == 1 {
				value = two
			}
			if err := directory.Replace("header", value); err != nil {
				writeErr <- err
				return
			}
		}
	}()
	for i := 0; i < 500; i++ {
		value, err := os.ReadFile(filepath.Join(path, "header"))
		if err != nil {
			t.Fatalf("external reader: %v", err)
		}
		if !valid(value) {
			t.Fatalf("reader observed partial replacement: %d bytes", len(value))
		}
	}
	writers.Wait()
	select {
	case err := <-writeErr:
		t.Fatal(err)
	default:
	}
}

func TestPinnedSocketPathSurvivesDirectoryCloseAndDescriptorReuse(t *testing.T) {
	directory, path := openTestDirectory(t)
	originalFD := directory.fd
	socket, err := directory.PinPath("admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer socket.Close()
	moved := path + "-moved"
	if err := os.Rename(path, moved); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	// This is the previously unsafe interleaving: obtain the pathname, close
	// its original owner, then bind. A new open may reuse the original FD.
	if err := directory.Close(); err != nil {
		t.Fatal(err)
	}
	replacement, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer replacement.Close()
	t.Logf("original FD %d; replacement FD %d; pin FD %d", originalFD, replacement.Fd(), socket.file.Fd())
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket.Path(), Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if _, err := os.Stat(filepath.Join(moved, "admin.sock")); err != nil {
		t.Fatalf("socket path did not use retained directory: %v", err)
	}
	if _, err := os.Lstat(filepath.Join(path, "admin.sock")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("socket escaped into replacement directory", err)
	}
	if _, err := directory.PinPath("admin.sock"); !errors.Is(err, ErrClosed) {
		t.Fatalf("PinPath after Close error = %v, want ErrClosed", err)
	}
}

func TestRetainedDirectorySurvivesPathRename(t *testing.T) {
	directory, path := openTestDirectory(t)
	if err := directory.Create("header", []byte("old")); err != nil {
		t.Fatal(err)
	}
	moved := path + "-moved"
	if err := os.Rename(path, moved); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	if err := directory.Replace("header", []byte("new")); err != nil {
		t.Fatal(err)
	}
	contents, err := os.ReadFile(filepath.Join(moved, "header"))
	if err != nil || string(contents) != "new" {
		t.Fatalf("retained directory contents = %q, %v", contents, err)
	}
	if _, err := os.Stat(filepath.Join(path, "header")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("replacement directory received a file: %v", err)
	}
}
