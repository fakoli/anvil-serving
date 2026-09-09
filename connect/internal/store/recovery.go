package store

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"strings"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	bolt "go.etcd.io/bbolt"
)

// MaxSnapshotBytes is a capacity bound, not a truncation policy. Backup refuses
// larger stores; callers must retain the previous complete backup on failure.
const MaxSnapshotBytes = 768 * 1024

type snapshotRecord struct {
	ID    string `json:"id"`
	Value string `json:"value"`
}

type snapshotBucket struct {
	Name    string           `json:"name"`
	Records []snapshotRecord `json:"records"`
}

type snapshot struct {
	Schema  string           `json:"schema"`
	Epoch   string           `json:"epoch"`
	Buckets []snapshotBucket `json:"buckets"`
}

func validEpoch(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 32 && hex.EncodeToString(decoded) == value
}

func validateBackupNamespaces(tx *bolt.Tx) error {
	allowed := map[string]bool{"meta": true}
	for _, name := range buckets {
		allowed[name] = true
	}
	if err := tx.ForEach(func(name []byte, _ *bolt.Bucket) error {
		if !allowed[string(name)] {
			return ErrState
		}
		delete(allowed, string(name))
		return nil
	}); err != nil || len(allowed) != 0 {
		return ErrState
	}
	meta := tx.Bucket([]byte("meta"))
	if string(meta.Get([]byte("schema"))) != "anvil-connect.authority/v1" || !validEpoch(string(meta.Get([]byte("epoch")))) {
		return ErrState
	}
	return meta.ForEach(func(key, value []byte) error {
		switch string(key) {
		case "schema", "epoch":
			return nil
		case "recovered_from":
			if validEpoch(string(value)) {
				return nil
			}
		}
		return ErrState
	})
}

// Snapshot copies a consistent logical view, never a live database pathname.
// It contains private authorization history and must not be logged or published.
func (s *Store) Snapshot() ([]byte, error) {
	value := snapshot{Schema: "anvil-connect.authority-backup/v1", Buckets: []snapshotBucket{}}
	err := s.db.View(func(tx *bolt.Tx) error {
		if err := validateBackupNamespaces(tx); err != nil {
			return err
		}
		value.Epoch = string(tx.Bucket([]byte("meta")).Get([]byte("epoch")))
		size := 0
		for index, name := range buckets {
			value.Buckets = append(value.Buckets, snapshotBucket{Name: name, Records: []snapshotRecord{}})
			if err := tx.Bucket([]byte(name)).ForEach(func(key, data []byte) error {
				size += len(key) + len(data)
				if len(key) < 1 || len(key) > 256 || len(data) > 65536 || !json.Valid(data) || size > MaxSnapshotBytes || len(value.Buckets[index].Records) >= 4096 {
					return ErrState
				}
				value.Buckets[index].Records = append(value.Buckets[index].Records, snapshotRecord{string(key), string(data)})
				return nil
			}); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return nil, ErrState
	}
	data, err := json.Marshal(value)
	if err != nil || len(data) > MaxSnapshotBytes {
		return nil, ErrState
	}
	if _, err := parseSnapshot(data); err != nil {
		return nil, ErrState
	}
	return data, nil
}

func parseSnapshot(data []byte) (snapshot, error) {
	var value snapshot
	if len(data) > MaxSnapshotBytes || config.Decode(bytes.NewReader(data), &value) != nil || value.Schema != "anvil-connect.authority-backup/v1" || !validEpoch(value.Epoch) || len(value.Buckets) != len(buckets) {
		return snapshot{}, ErrState
	}
	for index, name := range buckets {
		records := value.Buckets[index].Records
		if value.Buckets[index].Name != name || records == nil || len(records) > 4096 {
			return snapshot{}, ErrState
		}
		seen := map[string]bool{}
		for _, record := range records {
			if len(record.ID) < 1 || len(record.ID) > 256 || seen[record.ID] || len(record.Value) > 65536 || !json.Valid([]byte(record.Value)) {
				return snapshot{}, ErrState
			}
			seen[record.ID] = true
			if name == "principals" {
				var object map[string]json.RawMessage
				if json.Unmarshal([]byte(record.Value), &object) != nil || object == nil {
					return snapshot{}, ErrState
				}
			}
		}
	}
	return value, nil
}

// ValidateSnapshot checks the whole closed format without opening any state.
func ValidateSnapshot(data []byte) error {
	_, err := parseSnapshot(data)
	return err
}

// RestoreSnapshot operates only on an empty authority. One transaction assigns
// a new epoch and restores all human/API principal records disabled. Historical
// keys, installations, invitations, sessions, login transactions and replay
// records are deliberately not imported. The original backup remains the audit
// record; its former grants require an explicit owner decision to re-enable.
func (s *Store) RestoreSnapshot(data []byte) error {
	value, err := parseSnapshot(data)
	if err != nil {
		return err
	}
	epoch, err := randomEpoch()
	if err != nil || epoch == value.Epoch {
		return ErrState
	}
	return s.db.Update(func(tx *bolt.Tx) error {
		meta := tx.Bucket([]byte("meta"))
		if meta.Get([]byte("recovered_from")) != nil {
			return ErrState
		}
		for _, name := range buckets {
			key, _ := tx.Bucket([]byte(name)).Cursor().First()
			if key != nil {
				return ErrState
			}
		}
		for _, record := range value.Buckets[0].Records {
			var object map[string]json.RawMessage
			if json.Unmarshal([]byte(record.Value), &object) != nil {
				return ErrState
			}
			for key := range object {
				if strings.EqualFold(key, "disabled") {
					delete(object, key)
				}
			}
			object["disabled"] = json.RawMessage("true")
			encoded, err := json.Marshal(object)
			if err != nil || len(encoded) > 65536 {
				return ErrState
			}
			if err := tx.Bucket([]byte("principals")).Put([]byte(record.ID), encoded); err != nil {
				return ErrState
			}
		}
		if err := meta.Put([]byte("recovered_from"), []byte(value.Epoch)); err != nil {
			return ErrState
		}
		if err := meta.Put([]byte("epoch"), []byte(epoch)); err != nil {
			return ErrState
		}
		return nil
	})
}
