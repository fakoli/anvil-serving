package runtime

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	bolt "go.etcd.io/bbolt"
)

func TestGatewayRecoveryFencesBackupBeforePublishingAuthority(t *testing.T) {
	c := gatewaySettings(t)
	if err := InitializeGateway(c); err != nil {
		t.Fatal(err)
	}
	if _, err := BackupGateway(c); err == nil {
		t.Fatal("absent database silently initialized for backup")
	}
	state, err := store.Open(filepath.Join(c.StateDirectory, "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	old := ""
	if err := state.Update(func(tx *store.Tx) error {
		old = tx.Epoch()
		return tx.Put("principals", "human:subject", map[string]any{"disabled": false, "generation": 1, "resources": []string{"dashboard"}})
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := BackupGateway(c); err == nil {
		t.Fatal("backup raced open gateway authority")
	}
	if err := state.Close(); err != nil {
		t.Fatal(err)
	}
	data, err := BackupGateway(c)
	if err != nil {
		t.Fatal(err)
	}
	original, err := os.ReadFile(filepath.Join(c.StateDirectory, "authorities.json"))
	if err != nil {
		t.Fatal(err)
	}
	parent := t.TempDir()
	if err := os.Chmod(parent, 0700); err != nil {
		t.Fatal(err)
	}
	c.StateDirectory = filepath.Join(parent, "restored")
	corrupt := append([]byte(nil), data...)
	corrupt[len(corrupt)/2] ^= 1
	if RestoreGateway(c, corrupt, BackupDigest(data)) == nil {
		t.Fatal("corrupted archive accepted")
	}
	if _, err := os.Lstat(c.StateDirectory); !os.IsNotExist(err) {
		t.Fatal("digest failure touched destination")
	}
	wrong := c
	wrong.ControlHost = "wrong.example.test"
	if RestoreGateway(wrong, data, BackupDigest(data)) == nil {
		t.Fatal("archive rebound to another deployment")
	}
	if err := RestoreGateway(c, data, BackupDigest(data)); err != nil {
		t.Fatal(err)
	}
	if RestoreGateway(c, data, BackupDigest(data)) == nil {
		t.Fatal("existing state replaced")
	}
	dir, err := privatefiles.Open(c.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer dir.Close()
	if _, _, err := loadAuthorities(dir); err != nil {
		t.Fatal(err)
	}
	after, err := dir.Read("authorities.json", 32768)
	if err != nil || !bytes.Equal(original, after) {
		t.Fatal("authority identity changed")
	}
	state, err = store.Open(filepath.Join(c.StateDirectory, "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer state.Close()
	if err := state.View(func(tx *store.Tx) error {
		if tx.Epoch() == old {
			t.Fatal("historical epoch retained")
		}
		var human map[string]any
		if err := tx.Get("principals", "human:subject", &human); err != nil {
			return err
		}
		if human["disabled"] != true {
			t.Fatal("historical human grant reactivated")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestBackupRejectsIncompleteDatabaseWithoutRepair(t *testing.T) {
	c := gatewaySettings(t)
	if err := InitializeGateway(c); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(c.StateDirectory, "authority")
	if err := os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(path, "state.db")
	db, err := bolt.Open(file, 0600, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Update(func(tx *bolt.Tx) error { _, err := tx.CreateBucket([]byte("incomplete")); return err }); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(file)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := BackupGateway(c); err == nil {
		t.Fatal("incomplete state backed up")
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
		t.Fatal("backup repaired or initialized incomplete state")
	}
}

func TestRestoreMissingParentAndDirectorySyncFailureStayInert(t *testing.T) {
	c := gatewaySettings(t)
	if err := InitializeGateway(c); err != nil {
		t.Fatal(err)
	}
	state, err := store.Open(filepath.Join(c.StateDirectory, "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := state.Close(); err != nil {
		t.Fatal(err)
	}
	data, err := BackupGateway(c)
	if err != nil {
		t.Fatal(err)
	}
	missing := filepath.Join(t.TempDir(), "missing")
	c.StateDirectory = filepath.Join(missing, "restored")
	if RestoreGateway(c, data, BackupDigest(data)) == nil {
		t.Fatal("missing parent accepted")
	}
	if _, err := os.Lstat(missing); !os.IsNotExist(err) {
		t.Fatal("missing parent created by restore")
	}
	parent := t.TempDir()
	if err := os.Chmod(parent, 0700); err != nil {
		t.Fatal(err)
	}
	c.StateDirectory = filepath.Join(parent, "restored")
	err = restoreGateway(c, data, BackupDigest(data), func(path string) error {
		if filepath.Base(path) != "authority" {
			t.Fatal("wrong durability boundary")
		}
		return errors.New("injected directory sync failure")
	})
	if err == nil {
		t.Fatal("sync failure ignored")
	}
	if _, err := os.Lstat(filepath.Join(c.StateDirectory, "authorities.json")); !os.IsNotExist(err) {
		t.Fatal("failed restore became startable")
	}
	if RestoreGateway(c, data, BackupDigest(data)) == nil {
		t.Fatal("inert destination reused without inspection")
	}
}

func TestRecoveryRejectsInvalidCAWithoutCreatingDestination(t *testing.T) {
	c := gatewaySettings(t)
	if err := InitializeGateway(c); err != nil {
		t.Fatal(err)
	}
	state, err := store.Open(filepath.Join(c.StateDirectory, "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := state.Close(); err != nil {
		t.Fatal(err)
	}
	data, err := BackupGateway(c)
	if err != nil {
		t.Fatal(err)
	}
	data = bytes.Replace(data, []byte("PRIVATE KEY"), []byte("INVALID KEY"), 1)
	c.StateDirectory = filepath.Join(t.TempDir(), "restored")
	if RestoreGateway(c, data, BackupDigest(data)) == nil {
		t.Fatal("invalid archive CA accepted")
	}
	if _, err := os.Lstat(c.StateDirectory); !os.IsNotExist(err) {
		t.Fatal("invalid CA created destination")
	}
}
