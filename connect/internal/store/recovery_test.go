package store

import (
	"bytes"
	"encoding/json"
	"errors"
	bolt "go.etcd.io/bbolt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRecoveryDisablesGrantsAndDropsAllHistoricalAuthority(t *testing.T) {
	source, err := Open(filepath.Join(t.TempDir(), "source"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	old := ""
	if err := source.Update(func(tx *Tx) error {
		old = tx.Epoch()
		for _, bucket := range buckets {
			if err := tx.Put(bucket, "record", map[string]any{"disabled": false, "generation": 2, "resources": []string{"dashboard"}}); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	data, err := source.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if err := source.RestoreSnapshot(data); err == nil {
		t.Fatal("live state overwritten")
	}
	target, err := Open(filepath.Join(t.TempDir(), "target"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer target.Close()
	if err := target.RestoreSnapshot(data); err != nil {
		t.Fatal(err)
	}
	if err := target.RestoreSnapshot(data); err == nil {
		t.Fatal("repeat recovery allowed")
	}
	if err := target.View(func(tx *Tx) error {
		if tx.Epoch() == old {
			t.Fatal("restored epoch reused")
		}
		var record map[string]any
		if err := tx.Get("principals", "record", &record); err != nil {
			return err
		}
		if record["disabled"] != true || record["generation"] != float64(2) {
			t.Fatal("restored grant active or audit generation lost")
		}
		for _, bucket := range buckets[1:] {
			if !errors.Is(tx.Get(bucket, "record", &record), ErrMissing) {
				t.Fatal("historical authority restored", bucket)
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestBackupRejectsUnknownNamespacesWithoutDroppingData(t *testing.T) {
	for _, kind := range []string{"bucket", "metadata"} {
		t.Run(kind, func(t *testing.T) {
			directory := filepath.Join(t.TempDir(), "state")
			source, err := Open(directory, nil)
			if err != nil {
				t.Fatal(err)
			}
			if err := source.db.Update(func(tx *bolt.Tx) error {
				if kind == "metadata" {
					return tx.Bucket([]byte("meta")).Put([]byte("future"), []byte("private record"))
				}
				bucket, err := tx.CreateBucket([]byte("future"))
				if err != nil {
					return err
				}
				return bucket.Put([]byte("record"), []byte("private record"))
			}); err != nil {
				t.Fatal(err)
			}
			if data, err := source.Snapshot(); err == nil || data != nil {
				t.Fatal("snapshot silently omitted unknown namespace")
			}
			if err := source.Close(); err != nil {
				t.Fatal(err)
			}
			file := filepath.Join(directory, "state.db")
			before, err := os.ReadFile(file)
			if err != nil {
				t.Fatal(err)
			}
			info, err := os.Stat(file)
			if err != nil {
				t.Fatal(err)
			}
			if reopened, err := OpenExisting(directory, nil); err == nil {
				reopened.Close()
				t.Fatal("future namespace accepted")
			}
			after, err := os.ReadFile(file)
			if err != nil {
				t.Fatal(err)
			}
			now, err := os.Stat(file)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(before, after) || !info.ModTime().Equal(now.ModTime()) {
				t.Fatal("failed backup mutated source")
			}
		})
	}
}

func TestRecoveryRejectsMalformedArchiveBeforeMutation(t *testing.T) {
	source, err := Open(filepath.Join(t.TempDir(), "source"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	data, err := source.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	bad := [][]byte{
		bytes.Replace(data, []byte(`"schema":`), []byte(`"unknown":1,"schema":`), 1),
		bytes.Replace(data, []byte(`"schema":`), []byte(`"schema":"duplicate","schema":`), 1),
		bytes.Replace(data, []byte(`"principals"`), []byte(`"../principals"`), 1),
		bytes.Repeat([]byte(" "), MaxSnapshotBytes+1),
	}
	for _, input := range bad {
		if ValidateSnapshot(input) == nil || source.RestoreSnapshot(input) == nil {
			t.Fatal("malformed recovery accepted")
		}
	}
	if err := source.RestoreSnapshot(data); err != nil {
		t.Fatal("failed parse mutated empty target", err)
	}
}

func TestBackupCapacityFailsWithoutTruncation(t *testing.T) {
	source, err := Open(filepath.Join(t.TempDir(), "source"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	if err := source.Update(func(tx *Tx) error {
		for n := 0; n < 32; n++ {
			id, _ := json.Marshal(n)
			if err := tx.Put("replay", string(id), strings.Repeat("a", 32000)); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if data, err := source.Snapshot(); err == nil || data != nil {
		t.Fatal("partial oversized snapshot returned")
	}
}
