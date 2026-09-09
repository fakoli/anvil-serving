// Package store owns Connect's private transactional authority state. This
// database is separate from Anvil's project state and application credentials.
package store

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"time"

	bolt "go.etcd.io/bbolt"
	"golang.org/x/sys/unix"
)

var (
	ErrMissing = errors.New("state record missing")
	ErrState   = errors.New("private authority state unavailable or invalid")
)

var buckets = []string{"principals", "api_keys", "installations", "invitations", "sessions", "transactions", "replay"}

type Store struct {
	db   *bolt.DB
	root *os.Root
	now  func() time.Time
}

type Tx struct {
	tx  *bolt.Tx
	now time.Time
}

// Open requires an exclusively owned directory. It never makes a shared
// directory private by chmod, follows a database symlink or logs a record.
// Initial delivery targets Linux; cross-platform storage needs qualification.
func Open(directory string, now func() time.Time) (*Store, error) {
	if now == nil {
		now = time.Now
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, ErrState
	}
	info, err := os.Lstat(directory)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
		return nil, ErrState
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, ErrState
	}
	if err := validateRoot(root, info); err != nil {
		root.Close()
		return nil, err
	}
	db, err := bolt.Open(filepath.Join(directory, "state.db"), 0600, &bolt.Options{
		Timeout: time.Second,
		OpenFile: func(_ string, flags int, mode os.FileMode) (*os.File, error) {
			f, err := root.OpenFile("state.db", flags|unix.O_NOFOLLOW, mode)
			if err != nil {
				return nil, ErrState
			}
			info, err := f.Stat()
			var stat unix.Stat_t
			if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || unix.Fstat(int(f.Fd()), &stat) != nil || stat.Nlink != 1 || stat.Uid != uint32(os.Geteuid()) {
				f.Close()
				return nil, ErrState
			}
			return f, nil
		},
	})
	if err != nil {
		root.Close()
		return nil, ErrState
	}
	s := &Store{db: db, root: root, now: now}
	err = db.Update(func(tx *bolt.Tx) error {
		meta := tx.Bucket([]byte("meta"))
		if meta == nil {
			var err error
			meta, err = tx.CreateBucket([]byte("meta"))
			if err != nil {
				return err
			}
			if err := meta.Put([]byte("schema"), []byte("anvil-connect.authority/v1")); err != nil {
				return err
			}
			epoch, err := randomEpoch()
			if err != nil {
				return err
			}
			if err := meta.Put([]byte("epoch"), []byte(epoch)); err != nil {
				return err
			}
		} else if string(meta.Get([]byte("schema"))) != "anvil-connect.authority/v1" || len(meta.Get([]byte("epoch"))) != 64 {
			return ErrState
		}
		for _, name := range buckets {
			if _, err := tx.CreateBucketIfNotExists([]byte(name)); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		s.Close()
		return nil, ErrState
	}
	return s, nil
}

func validateRoot(root *os.Root, before os.FileInfo) error {
	opened, err := root.Stat(".")
	if err != nil || !opened.IsDir() || opened.Mode().Perm() != 0700 || !os.SameFile(before, opened) {
		return ErrState
	}
	stat, ok := opened.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return ErrState
	}
	return nil
}

func randomEpoch() (string, error) {
	var random [32]byte
	if _, err := rand.Read(random[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(random[:]), nil
}

func (s *Store) Close() error {
	err := s.db.Close()
	rootErr := s.root.Close()
	if err != nil || rootErr != nil {
		return ErrState
	}
	return nil
}

func (s *Store) View(fn func(*Tx) error) error {
	return s.db.View(func(tx *bolt.Tx) error { return fn(&Tx{tx: tx, now: s.now().UTC()}) })
}

func (s *Store) Update(fn func(*Tx) error) error {
	return s.db.Update(func(tx *bolt.Tx) error { return fn(&Tx{tx: tx, now: s.now().UTC()}) })
}

func (t *Tx) Now() time.Time { return t.now }
func (t *Tx) Epoch() string  { return string(t.tx.Bucket([]byte("meta")).Get([]byte("epoch"))) }

func (t *Tx) bucket(name string) (*bolt.Bucket, error) {
	for _, allowed := range buckets {
		if allowed == name {
			b := t.tx.Bucket([]byte(name))
			if b != nil {
				return b, nil
			}
		}
	}
	return nil, ErrState
}

func (t *Tx) Get(bucket, id string, result any) error {
	b, err := t.bucket(bucket)
	if err != nil {
		return err
	}
	value := b.Get([]byte(id))
	if value == nil {
		return ErrMissing
	}
	if len(value) > 65536 || json.Unmarshal(value, result) != nil {
		return ErrState
	}
	return nil
}

func (t *Tx) Put(bucket, id string, value any) error {
	if len(id) < 1 || len(id) > 256 {
		return ErrState
	}
	b, err := t.bucket(bucket)
	if err != nil {
		return err
	}
	encoded, err := json.Marshal(value)
	if err != nil || len(encoded) > 65536 {
		return ErrState
	}
	return b.Put([]byte(id), encoded)
}

func (t *Tx) Delete(bucket, id string) error {
	b, err := t.bucket(bucket)
	if err != nil {
		return err
	}
	return b.Delete([]byte(id))
}

// ResetAuthority invalidates all credentials carrying a prior epoch. Recovery
// must call this on the restored database before admitting any request; copying
// an old file alone is not a safe restore and is not claimed as one here.
func (s *Store) ResetAuthority() error {
	epoch, err := randomEpoch()
	if err != nil {
		return ErrState
	}
	return s.db.Update(func(tx *bolt.Tx) error {
		return tx.Bucket([]byte("meta")).Put([]byte("epoch"), []byte(epoch))
	})
}
