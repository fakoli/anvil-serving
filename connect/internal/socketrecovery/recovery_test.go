package socketrecovery

import (
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"testing"

	"golang.org/x/sys/unix"
)

func socketFixture(t *testing.T, name string) (string, uint32, func() *net.UnixListener) {
	t.Helper()
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	mode := uint32(0600)
	if name == "ingress.sock" {
		mode = 0660
		if err := os.Chmod(directory, os.ModeSetgid|0710); err != nil {
			t.Fatal(err)
		}
	}
	file, err := os.Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = file.Close() })
	path := fmt.Sprintf("/proc/self/fd/%d/%s", file.Fd(), name)
	listen := func() *net.UnixListener {
		listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
		if err != nil {
			t.Fatal(err)
		}
		listener.SetUnlinkOnClose(false)
		t.Cleanup(func() { _ = listener.Close() })
		if err := os.Chmod(path, os.FileMode(mode)); err != nil {
			t.Fatal(err)
		}
		return listener
	}
	return path, mode, listen
}

func TestOwnedCrashLeftoversArePreservedBeforeRebinding(t *testing.T) {
	for _, name := range []string{"admin.sock", "ingress.sock"} {
		t.Run(name, func(t *testing.T) {
			path, mode, listen := socketFixture(t, name)
			if err := listen().Close(); err != nil {
				t.Fatal(err)
			}
			before, err := os.Lstat(path)
			if err != nil {
				t.Fatal(err)
			}
			candidate, err := Inspect(path, uint32(os.Geteuid()), uint32(os.Getegid()), mode)
			if err != nil || candidate == nil {
				t.Fatal("refused owned socket unavailable", err)
			}
			if err := candidate.Quarantine(); err != nil {
				t.Fatal(err)
			}
			if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("old name remains", err)
			}
			entries, err := filepath.Glob(filepath.Join(filepath.Dir(path), "refused-sockets-*", name))
			if err != nil || len(entries) != 1 {
				t.Fatal("missing unique preservation", entries, err)
			}
			preserved, err := os.Lstat(entries[0])
			if err != nil || !os.SameFile(before, preserved) {
				t.Fatal("original inode not preserved", err)
			}
			directory, err := os.Stat(filepath.Dir(entries[0]))
			if err != nil || directory.Mode().Perm() != 0700 {
				t.Fatal("quarantine is not private", err)
			}
			listener := listen()
			if err := candidate.Quarantine(); err == nil {
				t.Fatal("old candidate moved new listener")
			}
			connection, err := net.Dial("unix", path)
			if err != nil {
				t.Fatal("new listener was displaced", err)
			}
			_ = connection.Close()
			_ = listener.Close()
		})
	}
}

func TestActiveListenersAndNonSocketEntriesAreUntouched(t *testing.T) {
	for _, kind := range []string{"active-admin", "active-ingress", "unknown-protocol", "regular", "symlink", "wrong-mode", "hardlink", "wrong-owner", "wrong-group"} {
		t.Run(kind, func(t *testing.T) {
			name := "admin.sock"
			if kind == "active-ingress" {
				name = "ingress.sock"
			}
			path, mode, listen := socketFixture(t, name)
			uid, gid := uint32(os.Geteuid()), uint32(os.Getegid())
			switch kind {
			case "regular":
				if err := os.WriteFile(path, []byte("preserve"), 0600); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Symlink("missing", path); err != nil {
					t.Fatal(err)
				}
			case "unknown-protocol":
				listener, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = listener.Close() })
				if err := os.Chmod(path, 0600); err != nil {
					t.Fatal(err)
				}
			default:
				listener := listen()
				if kind != "active-admin" && kind != "active-ingress" {
					_ = listener.Close()
				}
				if kind == "wrong-mode" {
					if err := os.Chmod(path, 0666); err != nil {
						t.Fatal(err)
					}
				}
				if kind == "hardlink" {
					if err := os.Link(path, path+".link"); err != nil {
						t.Fatal(err)
					}
				}
				if kind == "wrong-owner" {
					uid++
				}
				if kind == "wrong-group" {
					gid++
				}
			}
			before, err := os.Lstat(path)
			if err != nil {
				t.Fatal(err)
			}
			if candidate, err := Inspect(path, uid, gid, mode); err == nil || candidate != nil {
				t.Fatal("unsafe occupant admitted")
			}
			after, err := os.Lstat(path)
			if err != nil || !os.SameFile(before, after) {
				t.Fatal("unsafe occupant changed", err)
			}
		})
	}
}

func TestReplacementAndLostDescriptorRefuseRecovery(t *testing.T) {
	for _, kind := range []string{"replacement", "directory-mode", "descriptor-closed"} {
		t.Run(kind, func(t *testing.T) {
			path, mode, listen := socketFixture(t, "admin.sock")
			_ = listen().Close()
			candidate, err := Inspect(path, uint32(os.Geteuid()), uint32(os.Getegid()), mode)
			if err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "replacement":
				if err := os.Rename(path, path+".original"); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte("replacement"), 0600); err != nil {
					t.Fatal(err)
				}
			case "directory-mode":
				if err := os.Chmod(filepath.Dir(path), 0750); err != nil {
					t.Fatal(err)
				}
			case "descriptor-closed":
				// A descriptor path resolving to another directory must not reuse
				// an old candidate. Keep the fixture's descriptor itself intact.
				candidate.path = "/proc/self/fd/2147483647/admin.sock"
			}
			if err := candidate.Quarantine(); err == nil {
				t.Fatal("changed custody recovered")
			}
			if _, err := os.Lstat(path); err != nil {
				t.Fatal("refusal removed occupant", err)
			}
		})
	}
}

func TestMissingSocketAndMalformedPinsDoNotMutate(t *testing.T) {
	path, mode, _ := socketFixture(t, "admin.sock")
	if candidate, err := Inspect(path, uint32(os.Geteuid()), uint32(os.Getegid()), mode); err != nil || candidate != nil {
		t.Fatal(candidate, err)
	}
	for _, unsafe := range []string{filepath.Join(t.TempDir(), "admin.sock"), "/proc/self/fd/-1/admin.sock", "/proc/self/fd/01/admin.sock", filepath.Join(filepath.Dir(path), "other.sock")} {
		if _, err := Inspect(unsafe, uint32(os.Geteuid()), uint32(os.Getegid()), mode); !errors.Is(err, ErrRecovery) {
			t.Fatal("invalid pin accepted", err)
		}
	}
	var candidate *Refused
	if candidate.Quarantine() != nil {
		t.Fatal("absent candidate was not a no-op")
	}
	var value unix.Stat_t
	if unix.Lstat(path, &value) != unix.ENOENT {
		t.Fatal("missing socket created")
	}
}
