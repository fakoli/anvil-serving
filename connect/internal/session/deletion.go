package session

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"regexp"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

var deletionID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

const deletionPrefix = "human-delete:"

// Deletion is a durable, disabled-account intent consumed by the installed
// root account worker. Only the native authority can finalize it, after IdP
// removal. Completed receipts retain no username or principal.
type Deletion struct {
	RequestID  string `json:"request_id"`
	Principal  string `json:"principal,omitempty"`
	Username   string `json:"username,omitempty"`
	Generation uint64 `json:"generation,omitempty"`
	Epoch      string `json:"epoch"`
	Digest     string `json:"digest"`
	Complete   bool   `json:"complete"`
}

func deletionDigest(id, principal string, expected uint64) string {
	raw, _ := json.Marshal([]any{id, principal, expected})
	hash := sha256.Sum256(raw)
	return hex.EncodeToString(hash[:])
}

// PrepareDeletionTx fences all existing credentials before the external IdP
// operation. An outstanding deletion cannot be re-enabled or retargeted.
func (m *Manager) PrepareDeletionTx(tx *store.Tx, principal string, expected uint64, requestID string) (Deletion, error) {
	if !config.ValidHumanID(principal) || expected == 0 || !deletionID.MatchString(requestID) {
		return Deletion{}, ErrDenied
	}
	if m.administration != nil {
		for _, id := range m.administration.Operators {
			if id == principal {
				return Deletion{}, ErrDenied
			}
		}
	}
	digest := deletionDigest(requestID, principal, expected)
	var prior Deletion
	err := tx.Get("transactions", deletionPrefix+requestID, &prior)
	if err == nil {
		if prior.Epoch != tx.Epoch() || prior.RequestID != requestID || prior.Digest != digest {
			return Deletion{}, ErrConflict
		}
		return prior, nil
	}
	if !errors.Is(err, store.ErrMissing) {
		return Deletion{}, ErrUnavailable
	}
	var human Human
	if tx.Get("principals", principal, &human) != nil || human.ID != principal {
		return Deletion{}, ErrDenied
	}
	if human.Generation != expected || human.DeletionRequest != "" {
		return Deletion{}, ErrConflict
	}
	// ponytail: at most 1024 retained deletion receipts; prune explicitly during
	// coordinated authority maintenance if this personal deployment outgrows it.
	rows, err := tx.List("transactions", deletionPrefix, 1024)
	if err != nil || len(rows) >= 1024 {
		return Deletion{}, ErrUnavailable
	}
	pending := 0
	for _, row := range rows {
		var retained Deletion
		if json.Unmarshal(row.Value, &retained) != nil {
			return Deletion{}, ErrUnavailable
		}
		if !retained.Complete {
			pending++
		}
	}
	if pending >= 32 {
		return Deletion{}, ErrUnavailable
	}
	if err := m.UpdateHumanTx(tx, principal, expected, human.Resources, true); err != nil {
		return Deletion{}, err
	}
	if tx.Get("principals", principal, &human) != nil {
		return Deletion{}, ErrUnavailable
	}
	human.DeletionRequest = requestID
	intent := Deletion{RequestID: requestID, Principal: principal, Username: human.Username, Generation: human.Generation, Epoch: tx.Epoch(), Digest: digest}
	if tx.Put("principals", principal, human) != nil || tx.Put("transactions", deletionPrefix+requestID, intent) != nil {
		return Deletion{}, ErrUnavailable
	}
	return intent, nil
}

func (m *Manager) PrepareDeletion(principal string, expected uint64, requestID string) (Deletion, error) {
	var result Deletion
	err := m.state.Update(func(tx *store.Tx) error {
		var err error
		result, err = m.PrepareDeletionTx(tx, principal, expected, requestID)
		return err
	})
	return result, err
}

func (m *Manager) Deletions() ([]Deletion, error) {
	result := []Deletion{}
	err := m.state.View(func(tx *store.Tx) error {
		rows, err := tx.List("transactions", deletionPrefix, 1024)
		if err != nil {
			return ErrUnavailable
		}
		for _, row := range rows {
			var intent Deletion
			if json.Unmarshal(row.Value, &intent) != nil || row.ID != deletionPrefix+intent.RequestID || !deletionID.MatchString(intent.RequestID) || intent.Epoch != tx.Epoch() || len(intent.Digest) != 64 {
				return ErrUnavailable
			}
			if !intent.Complete {
				var human Human
				if tx.Get("principals", intent.Principal, &human) != nil || !human.Disabled || human.DeletionRequest != intent.RequestID || human.Generation != intent.Generation || human.Username != intent.Username {
					return ErrUnavailable
				}
				result = append(result, intent)
			}
		}
		return nil
	})
	return result, err
}

// InspectHuman is local-administration only; it exposes no session or subject.
func (m *Manager) InspectHuman(principal string) (Human, error) {
	if !config.ValidHumanID(principal) {
		return Human{}, ErrDenied
	}
	var human Human
	err := m.state.View(func(tx *store.Tx) error { return tx.Get("principals", principal, &human) })
	return human, err
}

func (m *Manager) FinalizeDeletion(requestID, principal string, expected uint64) error {
	if !deletionID.MatchString(requestID) || !config.ValidHumanID(principal) || expected == 0 {
		return ErrDenied
	}
	return m.state.Update(func(tx *store.Tx) error {
		var intent Deletion
		if tx.Get("transactions", deletionPrefix+requestID, &intent) != nil || intent.RequestID != requestID || intent.Epoch != tx.Epoch() {
			return ErrDenied
		}
		if expected < 2 || intent.Digest != deletionDigest(requestID, principal, expected-1) {
			return ErrConflict
		}
		if intent.Complete {
			return nil
		}
		if intent.Principal != principal || intent.Generation != expected {
			return ErrConflict
		}
		var human Human
		if tx.Get("principals", principal, &human) != nil || !human.Disabled || human.DeletionRequest != requestID || human.Generation != expected {
			return ErrConflict
		}
		if m.administration != nil {
			for _, id := range m.administration.Operators {
				if id == principal {
					return ErrDenied
				}
			}
		}
		if err := purgeHumanRecords(tx, principal); err != nil {
			return err
		}
		if tx.Delete("principals", principal) != nil {
			return ErrUnavailable
		}
		return tx.Put("transactions", deletionPrefix+requestID, Deletion{RequestID: requestID, Epoch: tx.Epoch(), Digest: intent.Digest, Complete: true})
	})
}

func purgeHumanRecords(tx *store.Tx, principal string) error {
	// ponytail: bounded full scans for this small authority store; add a native
	// reverse index before supporting more than 4096 records per namespace.
	for _, scope := range []struct{ bucket, prefix string }{{"sessions", "session:"}, {"api_keys", ""}, {"transactions", "device:"}, {"transactions", "administration-action:"}} {
		rows, err := deletionRecords(tx, scope.bucket, scope.prefix)
		if err != nil {
			return ErrUnavailable
		}
		for _, row := range rows {
			remove := false
			switch scope.prefix {
			case "session:":
				var value Session
				if json.Unmarshal(row.Value, &value) != nil || row.ID != "session:"+value.ID {
					return ErrUnavailable
				}
				remove = value.Principal == principal
			case "":
				var value access.Key
				if json.Unmarshal(row.Value, &value) != nil || row.ID != value.ID {
					return ErrUnavailable
				}
				remove = value.DeviceHuman == principal
			case "device:":
				var value struct {
					ID         string   `json:"id"`
					HumanID    string   `json:"human_id"`
					UserDigest [32]byte `json:"user_digest"`
				}
				if json.Unmarshal(row.Value, &value) != nil || row.ID != value.ID {
					return ErrUnavailable
				}
				remove = value.HumanID == principal
				if remove {
					index := "device-user:" + hex.EncodeToString(value.UserDigest[:])
					var indexed string
					err := tx.Get("transactions", index, &indexed)
					if err == nil && indexed == row.ID {
						if tx.Delete("transactions", index) != nil {
							return ErrUnavailable
						}
					} else if err != nil && !errors.Is(err, store.ErrMissing) {
						return ErrUnavailable
					}
				}
			case "administration-action:":
				var value struct {
					Actor  string `json:"actor"`
					Target string `json:"target"`
				}
				if json.Unmarshal(row.Value, &value) != nil {
					return ErrUnavailable
				}
				remove = value.Actor == principal || value.Target == principal
			}
			if remove && tx.Delete(scope.bucket, row.ID) != nil {
				return ErrUnavailable
			}
		}
	}
	return nil
}

func deletionRecords(tx *store.Tx, bucket, prefix string) ([]store.Record, error) {
	if prefix != "" {
		return tx.List(bucket, prefix, 4096)
	}
	rows := []store.Record{}
	after := ""
	for len(rows) < 4096 {
		page, more, err := tx.Page(bucket, prefix, after, 256)
		if err != nil {
			return nil, err
		}
		rows = append(rows, page...)
		if !more {
			return rows, nil
		}
		after = page[len(page)-1].ID
	}
	return nil, ErrUnavailable
}
