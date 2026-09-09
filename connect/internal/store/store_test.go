package store

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestAtomicPersistenceAndEpoch(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "authority")
	now := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	s, err := Open(directory, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	var first string
	if err := s.Update(func(tx *Tx) error {
		first = tx.Epoch()
		if !tx.Now().Equal(now) {
			t.Fatal("clock not injected")
		}
		return tx.Put("principals", "owner", map[string]int{"generation": 1})
	}); err != nil {
		t.Fatal(err)
	}
	rollback := errors.New("abort synthetic transaction")
	if err := s.Update(func(tx *Tx) error {
		if err := tx.Put("principals", "owner", map[string]int{"generation": 2}); err != nil {
			return err
		}
		return rollback
	}); !errors.Is(err, rollback) {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = Open(directory, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.View(func(tx *Tx) error {
		var value map[string]int
		if err := tx.Get("principals", "owner", &value); err != nil {
			return err
		}
		if value["generation"] != 1 || tx.Epoch() != first {
			t.Fatal("transaction rollback or restart corrupted state")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	if err := s.View(func(tx *Tx) error {
		if tx.Epoch() == first || len(tx.Epoch()) != 64 {
			t.Fatal("authority epoch reused")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(filepath.Join(directory, "state.db"))
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("state permissions not private")
	}
}

func TestRefusesUnsafeStatePaths(t *testing.T) {
	for _, kind := range []string{"public-directory", "public-file", "symlink", "hardlink"} {
		t.Run(kind, func(t *testing.T) {
			directory := t.TempDir()
			if err := os.Chmod(directory, 0700); err != nil {
				t.Fatal(err)
			}
			db := filepath.Join(directory, "state.db")
			switch kind {
			case "public-directory":
				if err := os.Chmod(directory, 0755); err != nil {
					t.Fatal(err)
				}
			case "public-file":
				if err := os.WriteFile(db, nil, 0644); err != nil {
					t.Fatal(err)
				}
			case "symlink", "hardlink":
				target := filepath.Join(t.TempDir(), "private-file")
				if err := os.WriteFile(target, []byte("sentinel"), 0600); err != nil {
					t.Fatal(err)
				}
				var err error
				if kind == "symlink" {
					err = os.Symlink(target, db)
				} else {
					err = os.Link(target, db)
				}
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					data, err := os.ReadFile(target)
					if err != nil || string(data) != "sentinel" {
						t.Error("unsafe target was modified")
					}
				})
			}
			if s, err := Open(directory, nil); err == nil {
				s.Close()
				t.Fatal("unsafe state path accepted")
			}
		})
	}
}

func TestRecordBoundsAndPrivateMetadata(t *testing.T) {
	s, err := Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.Update(func(tx *Tx) error {
		if tx.Put("meta", "epoch", "forged") == nil {
			t.Fatal("metadata exposed through generic record API")
		}
		if tx.Put("principals", "oversized", make([]byte, 65536)) == nil {
			t.Fatal("oversized record accepted")
		}
		var missing map[string]int
		if !errors.Is(tx.Get("principals", "absent", &missing), ErrMissing) {
			t.Fatal("missing record not identified")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestOpenedDirectoryIdentity(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "authority")
	if err := os.Mkdir(directory, 0700); err != nil {
		t.Fatal(err)
	}
	before, err := os.Lstat(directory)
	if err != nil {
		t.Fatal(err)
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	if err := validateRoot(root, before); err != nil {
		t.Fatal(err)
	}
	// Reproduce the state at a path-swap boundary deterministically: the
	// inspected directory and subsequently opened directory have different inodes.
	if err := os.Rename(directory, directory+"-old"); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(directory, 0700); err != nil {
		t.Fatal(err)
	}
	replaced, err := os.OpenRoot(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer replaced.Close()
	if validateRoot(replaced, before) == nil {
		t.Fatal("replacement directory accepted after inspection")
	}
	if validateRoot(root, before) != nil {
		t.Fatal("stable opened directory handle lost its identity")
	}
}

func TestWrongOwner(t *testing.T) {
	if os.Geteuid() != 0 {
		t.Skip("wrong-owner creation requires privilege; no privilege escalation in this test")
	}
	directory := filepath.Join(t.TempDir(), "authority")
	if err := os.Mkdir(directory, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(directory, 65534, -1); err != nil {
		t.Fatal(err)
	}
	if s, err := Open(directory, nil); err == nil {
		s.Close()
		t.Fatal("other user's authority directory accepted by privileged process")
	}
}
