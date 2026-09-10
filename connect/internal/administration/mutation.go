package administration

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"sort"
	"strconv"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

type Mutation struct {
	Action             string   `json:"action"`
	RequestID          string   `json:"request_id"`
	ExpectedGeneration string   `json:"expected_generation"`
	Principal          string   `json:"principal,omitempty"`
	Disabled           *bool    `json:"disabled,omitempty"`
	Resources          []string `json:"resources,omitempty"`
	SessionType        string   `json:"session_type,omitempty"`
	SessionID          string   `json:"session_id,omitempty"`
}

var requestIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
var objectIDPattern = regexp.MustCompile(`^[0-9a-f]{32}$`)

const mutationPrefix = "administration-action:"
const mutationLimit = 1024

type mutationRecord struct {
	Actor     string    `json:"actor"`
	SessionID string    `json:"session_id"`
	RequestID string    `json:"request_id"`
	Digest    string    `json:"digest"`
	Action    string    `json:"action"`
	Target    string    `json:"target"`
	IssuedAt  time.Time `json:"issued_at"`
	ExpiresAt time.Time `json:"expires_at"`
	Epoch     string    `json:"epoch"`
}

// Mutate checks acting authority, target generation and the last-operator
// invariant within the same transaction that records the effect and receipt.
func (a *Authority) Mutate(ctx context.Context, admitted session.Admission, request Mutation) error {
	if ctx == nil {
		return ErrInvalid
	}
	generation, err := strconv.ParseUint(request.ExpectedGeneration, 10, 64)
	if err != nil || generation == 0 || strconv.FormatUint(generation, 10) != request.ExpectedGeneration || !requestIDPattern.MatchString(request.RequestID) {
		return ErrInvalid
	}
	target := ""
	switch request.Action {
	case "human-update":
		if !config.ValidHumanID(request.Principal) || request.Disabled == nil || len(request.Resources) < 1 || len(request.Resources) > 64 || request.SessionType != "" || request.SessionID != "" {
			return ErrInvalid
		}
		request.Resources = append([]string(nil), request.Resources...)
		sort.Strings(request.Resources)
		for i, resource := range request.Resources {
			rule, ok := a.rules[resource]
			if !ok || rule.Access != "browser" || (i > 0 && request.Resources[i-1] == resource) {
				return ErrInvalid
			}
		}
		target = request.Principal
	case "session-revoke":
		if request.Principal != "" || request.Disabled != nil || request.Resources != nil || !objectIDPattern.MatchString(request.SessionID) || (request.SessionType != "browser" && request.SessionType != "terminal") {
			return ErrInvalid
		}
		target = request.SessionType + ":" + request.SessionID
	default:
		return ErrInvalid
	}
	raw, err := json.Marshal(request)
	if err != nil {
		return ErrInvalid
	}
	digest := sha256.Sum256(raw)
	hash := hex.EncodeToString(digest[:])
	err = a.state.Update(func(tx *store.Tx) error {
		if ctx.Err() != nil {
			return ErrUnavailable
		}
		if err := a.authorize(tx, admitted); err != nil {
			return err
		}
		records, err := tx.List("transactions", mutationPrefix, mutationLimit)
		if err != nil {
			return ErrUnavailable
		}
		active := 0
		for _, row := range records {
			if ctx.Err() != nil {
				return ErrUnavailable
			}
			var previous mutationRecord
			if json.Unmarshal(row.Value, &previous) != nil || row.ID != mutationPrefix+previous.RequestID || !requestIDPattern.MatchString(previous.RequestID) || !config.ValidHumanID(previous.Actor) || !objectIDPattern.MatchString(previous.SessionID) || len(previous.Digest) != 64 || !previous.ExpiresAt.After(previous.IssuedAt) || previous.ExpiresAt.Sub(previous.IssuedAt) > 24*time.Hour {
				return ErrUnavailable
			}
			if previous.Epoch != tx.Epoch() || !tx.Now().Before(previous.ExpiresAt) {
				if tx.Delete("transactions", row.ID) != nil {
					return ErrUnavailable
				}
				continue
			}
			active++
			if previous.RequestID == request.RequestID {
				if previous.Actor == admitted.Principal && previous.SessionID == admitted.SessionID && previous.Digest == hash {
					return nil
				}
				return ErrConflict
			}
		}
		if active >= mutationLimit {
			return ErrUnavailable
		}
		switch request.Action {
		case "human-update":
			if err := a.sessions.UpdateHumanTx(tx, request.Principal, generation, request.Resources, *request.Disabled); err != nil {
				if errors.Is(err, session.ErrConflict) {
					return ErrConflict
				}
				if errors.Is(err, session.ErrDenied) {
					return ErrDenied
				}
				return ErrUnavailable
			}
		case "session-revoke":
			if err := a.revoke(tx, request.SessionType, request.SessionID, generation); err != nil {
				return err
			}
		}
		if ctx.Err() != nil {
			return ErrUnavailable
		}
		receipt := mutationRecord{Actor: admitted.Principal, SessionID: admitted.SessionID, RequestID: request.RequestID, Digest: hash, Action: request.Action, Target: target, IssuedAt: tx.Now(), ExpiresAt: tx.Now().Add(24 * time.Hour), Epoch: tx.Epoch()}
		if tx.Put("transactions", mutationPrefix+request.RequestID, receipt) != nil {
			return ErrUnavailable
		}
		return nil
	})
	if err != nil && !errors.Is(err, ErrDenied) && !errors.Is(err, ErrConflict) && !errors.Is(err, ErrInvalid) {
		return ErrUnavailable
	}
	return err
}

func (a *Authority) revoke(tx *store.Tx, kind, id string, expected uint64) error {
	if kind == "browser" {
		var current session.Session
		if tx.Get("sessions", "session:"+id, &current) != nil || current.ID != id || !config.ValidHumanID(current.Principal) || current.Generation == 0 {
			return ErrDenied
		}
		if current.Generation != expected {
			return ErrConflict
		}
		if current.Generation == math.MaxUint64 {
			return ErrUnavailable
		}
		current.Generation++
		current.Revoked = true
		if tx.Put("sessions", "session:"+id, current) != nil {
			return ErrUnavailable
		}
		return nil
	}
	var key access.Key
	if tx.Get("api_keys", id, &key) != nil || key.ID != id || key.DeviceHuman == "" {
		return ErrDenied
	}
	if _, err := a.deviceKeyStatus(tx, key); err != nil {
		return err
	}
	if key.Generation != expected {
		return ErrConflict
	}
	if key.Generation == math.MaxUint64 {
		return ErrUnavailable
	}
	key.Generation++
	key.Revoked = true
	if tx.Put("api_keys", id, key) != nil {
		return ErrUnavailable
	}
	return nil
}
