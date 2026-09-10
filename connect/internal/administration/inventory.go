package administration

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"strconv"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

type userItem struct {
	ID            string   `json:"id"`
	Generation    string   `json:"generation"`
	Disabled      bool     `json:"disabled"`
	Resources     []string `json:"resources"`
	Administrator bool     `json:"administrator"`
}
type sessionItem struct {
	ID            string    `json:"id"`
	Type          string    `json:"type"`
	Principal     string    `json:"principal"`
	Resource      string    `json:"resource"`
	Status        string    `json:"status"`
	IssuedAt      time.Time `json:"issued_at"`
	ExpiresAt     time.Time `json:"expires_at"`
	Generation    string    `json:"generation"`
	SourceSession string    `json:"source_session,omitempty"`
}

func encodeCursor(kind, phase, key string) *string {
	v := base64.RawURLEncoding.EncodeToString([]byte(kind + "\x00" + phase + "\x00" + key))
	return &v
}
func decodeCursor(kind, cursor string) (string, string, error) {
	phase := "human"
	if kind == "sessions" {
		phase = "browser"
	}
	if cursor == "" {
		return phase, "", nil
	}
	if len(cursor) > 256 {
		return "", "", ErrInvalid
	}
	raw, err := base64.RawURLEncoding.DecodeString(cursor)
	if err != nil || base64.RawURLEncoding.EncodeToString(raw) != cursor {
		return "", "", ErrInvalid
	}
	parts := strings.Split(string(raw), "\x00")
	if len(parts) != 3 || parts[0] != kind {
		return "", "", ErrInvalid
	}
	if kind == "users" {
		if parts[1] != "human" || !config.ValidHumanID(parts[2]) {
			return "", "", ErrInvalid
		}
	} else {
		if parts[1] != "browser" && parts[1] != "terminal" {
			return "", "", ErrInvalid
		}
		if parts[2] != "" && !objectIDPattern.MatchString(parts[2]) {
			return "", "", ErrInvalid
		}
	}
	return parts[1], parts[2], nil
}

func (a *Authority) List(ctx context.Context, admitted session.Admission, kind, cursor string, limit int) (Inventory, error) {
	if ctx == nil || (kind != "users" && kind != "sessions") || limit < 1 || limit > 50 {
		return Inventory{}, ErrInvalid
	}
	phase, after, err := decodeCursor(kind, cursor)
	if err != nil {
		return Inventory{}, err
	}
	result := Inventory{Kind: kind, Items: []any{}}
	err = a.state.View(func(tx *store.Tx) error {
		if ctx.Err() != nil {
			return ErrUnavailable
		}
		if err := a.authorize(tx, admitted); err != nil {
			return err
		}
		if kind == "users" {
			records, more, err := tx.Page("principals", "human:", after, limit)
			if err != nil {
				return ErrUnavailable
			}
			for _, record := range records {
				if ctx.Err() != nil {
					return ErrUnavailable
				}
				var human session.Human
				if json.Unmarshal(record.Value, &human) != nil || human.ID != record.ID || !config.ValidHumanID(human.ID) || human.Generation == 0 || len(human.Resources) < 1 || len(human.Resources) > 64 {
					return ErrUnavailable
				}
				seen := map[string]bool{}
				for _, resource := range human.Resources {
					rule, configured := a.rules[resource]
					if !config.ValidID(resource) || seen[resource] || !configured || rule.Access != "browser" {
						return ErrUnavailable
					}
					seen[resource] = true
				}
				administrator := false
				for _, id := range a.settings.Operators {
					if id == human.ID {
						administrator = true
					}
				}
				result.Items = append(result.Items, userItem{human.ID, strconv.FormatUint(human.Generation, 10), human.Disabled, append([]string(nil), human.Resources...), administrator})
			}
			if more {
				result.NextCursor = encodeCursor(kind, phase, records[len(records)-1].ID)
			}
			return nil
		}
		if phase == "browser" {
			rawAfter := ""
			if after != "" {
				rawAfter = "session:" + after
			}
			records, more, err := tx.Page("sessions", "session:", rawAfter, limit)
			if err != nil {
				return ErrUnavailable
			}
			for _, record := range records {
				if ctx.Err() != nil {
					return ErrUnavailable
				}
				var current session.Session
				if json.Unmarshal(record.Value, &current) != nil || record.ID != "session:"+current.ID || !objectIDPattern.MatchString(current.ID) || !config.ValidHumanID(current.Principal) || current.Generation == 0 || !config.ValidID(current.Resource) || !validTimes(current.IssuedAt, current.ExpiresAt) {
					return ErrUnavailable
				}
				status := issuedStatus(current.Revoked, current.ExpiresAt, tx.Now())
				if status == "issued" && a.sessions.CheckTx(tx, session.Admission{SessionID: current.ID, SessionGeneration: current.Generation, Principal: current.Principal, PrincipalGeneration: current.PrincipalGeneration, Resource: current.Resource, Host: current.Host, Epoch: current.Epoch, ExpiresAt: current.ExpiresAt}) != nil {
					status = "invalidated"
				}
				result.Items = append(result.Items, sessionItem{current.ID, "browser", current.Principal, current.Resource, status, current.IssuedAt, current.ExpiresAt, strconv.FormatUint(current.Generation, 10), ""})
			}
			if more {
				result.NextCursor = encodeCursor(kind, phase, strings.TrimPrefix(records[len(records)-1].ID, "session:"))
				return nil
			}
			if len(result.Items) == limit {
				result.NextCursor = encodeCursor(kind, "terminal", "")
				return nil
			}
			phase = "terminal"
			after = ""
		}
		// Manual API keys can make this page sparse. Advance the opaque cursor even
		// when none of the bounded scanned records are device-issued sessions.
		records, more, err := tx.Page("api_keys", "", after, 128)
		if err != nil {
			return ErrUnavailable
		}
		last := after
		for i, record := range records {
			if ctx.Err() != nil {
				return ErrUnavailable
			}
			last = record.ID
			var key access.Key
			if json.Unmarshal(record.Value, &key) != nil || !objectIDPattern.MatchString(record.ID) || key.ID != record.ID {
				return ErrUnavailable
			}
			if key.DeviceHuman == "" {
				continue
			}
			status, err := a.deviceKeyStatus(tx, key)
			if err != nil {
				return err
			}
			result.Items = append(result.Items, sessionItem{key.ID, "terminal", key.DeviceHuman, key.Grants[0].Resource, status, key.IssuedAt, key.ExpiresAt, strconv.FormatUint(key.Generation, 10), key.DeviceSession})
			if len(result.Items) == limit {
				if more || i < len(records)-1 {
					result.NextCursor = encodeCursor(kind, phase, last)
				}
				return nil
			}
		}
		if more {
			result.NextCursor = encodeCursor(kind, phase, last)
		}
		return nil
	})
	if err != nil {
		return Inventory{}, err
	}
	return result, nil
}
func validTimes(issued, expires time.Time) bool {
	return !issued.IsZero() && expires.After(issued) && expires.Sub(issued) <= access.MaximumKeyLifetime
}
func issuedStatus(revoked bool, expires, now time.Time) string {
	if revoked {
		return "revoked"
	}
	if !now.Before(expires) {
		return "expired"
	}
	return "issued"
}
