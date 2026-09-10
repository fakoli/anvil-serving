// Package device implements Connect-owned, browser-approved device access.
// It stores only hashes of the device and user codes. The final API bearer is
// generated exactly once during redemption and is never persisted here.
package device

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base32"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

var (
	ErrDenied      = errors.New("device authorization denied")
	ErrSlowDown    = errors.New("device polling slowed")
	ErrUnavailable = errors.New("device authorization unavailable")
)

const (
	Lifetime          = 10 * time.Minute
	PollInterval      = 5 * time.Second
	CredentialLife    = time.Hour
	maxPending        = 128
	maxPolls          = 120
	maxApprovals      = 4
	terminalRetention = time.Minute
)

type Binding struct {
	Browser    config.Rule
	API        config.Rule
	Methods    []string
	Label      string
	Principals map[string]string
	Hash       string
}

type record struct {
	ID                string    `json:"id"`
	DeviceDigest      [32]byte  `json:"device_digest"`
	UserDigest        [32]byte  `json:"user_digest"`
	BindingHash       string    `json:"binding_hash"`
	BrowserResource   string    `json:"browser_resource"`
	APIResource       string    `json:"api_resource"`
	Methods           []string  `json:"methods"`
	IssuedAt          time.Time `json:"issued_at"`
	ExpiresAt         time.Time `json:"expires_at"`
	Status            string    `json:"status"`
	LastPoll          time.Time `json:"last_poll"`
	Polls             int       `json:"polls"`
	Cancels           int       `json:"cancels"`
	Approvals         int       `json:"approvals"`
	HumanID           string    `json:"human_id"`
	HumanGeneration   uint64    `json:"human_generation"`
	APIPrincipal      string    `json:"api_principal"`
	SessionExpiresAt  time.Time `json:"session_expires_at"`
	SessionID         string    `json:"session_id"`
	SessionGeneration uint64    `json:"session_generation"`
}

type Start struct {
	DeviceCode, UserCode string
	ExpiresAt            time.Time
}
type Poll struct {
	Status    string
	Secret    string
	ExpiresAt time.Time
}

type Authority struct {
	state              *store.Store
	keys               *access.Keys
	sessions           HumanAuthority
	byBrowser          map[string]Binding
	byHash             map[string]Binding
	mu                 sync.Mutex
	tokens             int
	lastRefill         time.Time
	approvals          map[string]approvalCounter
	terminalRequests   map[string]terminalCounter
	approvalTokens     int
	lastApprovalRefill time.Time
}

type approvalCounter struct {
	count   int
	expires time.Time
}
type terminalCounter struct {
	count   int
	expires time.Time
}

type HumanAuthority interface {
	Check(session.Admission) error
	CheckPrincipal(string, uint64) error
	CheckDeviceSession(string, uint64, string, uint64) error
}

func New(state *store.Store, gateway config.Gateway, sessions HumanAuthority, keys *access.Keys) (*Authority, error) {
	if state == nil || sessions == nil || keys == nil || gateway.Validate() != nil || len(gateway.DeviceAuthorizations) == 0 {
		return nil, ErrDenied
	}
	resources := map[string]config.Resource{}
	for _, resource := range gateway.Resources {
		resources[resource.Rule.ID] = resource
	}
	a := &Authority{state: state, keys: keys, sessions: sessions, byBrowser: map[string]Binding{}, byHash: map[string]Binding{}, tokens: 16, lastRefill: time.Now(), approvals: map[string]approvalCounter{}, terminalRequests: map[string]terminalCounter{}, approvalTokens: 32, lastApprovalRefill: time.Now()}
	for _, declared := range gateway.DeviceAuthorizations {
		browser, api := resources[declared.BrowserResource], resources[declared.APIResource]
		binding := Binding{Browser: browser.Rule, API: api.Rule, Methods: append([]string(nil), declared.Methods...), Label: declared.Label, Principals: map[string]string{}}
		for human, principal := range declared.Principals {
			binding.Principals[human] = principal
		}
		binding.Hash = bindingHash(binding)
		if binding.Hash == "" {
			return nil, ErrDenied
		}
		a.byBrowser[binding.Browser.ID], a.byHash[binding.Hash] = binding, binding
	}
	if err := keys.SetDeviceChecker(a.checkCredential); err != nil {
		return nil, ErrDenied
	}
	return a, nil
}

func bindingHash(binding Binding) string {
	if binding.Browser.ID == "" || binding.API.ID == "" || len(binding.Methods) == 0 {
		return ""
	}
	methods := append([]string(nil), binding.Methods...)
	sort.Strings(methods)
	humans := make([]string, 0, len(binding.Principals))
	for human := range binding.Principals {
		humans = append(humans, human)
	}
	sort.Strings(humans)
	var value strings.Builder
	value.WriteString(binding.Browser.ID)
	value.WriteByte('\x00')
	value.WriteString(binding.API.ID)
	value.WriteByte('\x00')
	value.WriteString(strings.Join(methods, ","))
	value.WriteByte('\x00')
	value.WriteString(binding.Label)
	for _, human := range humans {
		value.WriteByte('\x00')
		value.WriteString(human)
		value.WriteByte('=')
		value.WriteString(binding.Principals[human])
	}
	digest := sha256.Sum256([]byte(value.String()))
	return hex.EncodeToString(digest[:])
}

func pathFor(rule config.Rule) string {
	if rule.PathPrefix == "/" {
		return "/_anvil-connect/device"
	}
	return rule.PathPrefix + "/_anvil-connect/device"
}

func (a *Authority) Path(resource string) (string, bool) {
	b, ok := a.byBrowser[resource]
	return pathFor(b.Browser), ok
}
func (a *Authority) Binding(resource string) (Binding, bool) {
	b, ok := a.byBrowser[resource]
	return b, ok
}

// CSRF returns a session-bound, binding-bound confirmation value for the
// approval form. Session IDs are opaque host-only random identifiers and are
// never exposed to script; this value is not an authorization credential.
func (a *Authority) CSRF(admitted session.Admission, binding Binding) string {
	digest := sha256.Sum256([]byte(admitted.SessionID + "\x00" + binding.Hash))
	return hex.EncodeToString(digest[:])
}

func (a *Authority) ValidCSRF(admitted session.Admission, binding Binding, value string) bool {
	expected := a.CSRF(admitted, binding)
	return len(value) == len(expected) && subtle.ConstantTimeCompare([]byte(value), []byte(expected)) == 1
}

func randomCode(bytes int) (string, error) {
	raw := make([]byte, bytes)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(raw), nil
}

var codeGenerator = randomCode

func digest(value string) [32]byte { return sha256.Sum256([]byte(value)) }

const transactionBucket = "transactions"

func recordID(digest [32]byte) string { return "device:" + hex.EncodeToString(digest[:]) }
func userID(digest [32]byte) string   { return "device-user:" + hex.EncodeToString(digest[:]) }

func (a *Authority) takeStartToken(now time.Time) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	add := int(now.Sub(a.lastRefill) / time.Minute)
	if add > 0 {
		a.tokens = min(16, a.tokens+add)
		a.lastRefill = a.lastRefill.Add(time.Duration(add) * time.Minute)
	}
	if a.tokens == 0 {
		return false
	}
	a.tokens--
	return true
}

func (a *Authority) takeApprovalToken(sessionID string, expires time.Time, now time.Time) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	for id, counter := range a.approvals {
		if !now.Before(counter.expires) {
			delete(a.approvals, id)
		}
	}
	add := int(now.Sub(a.lastApprovalRefill) / time.Minute)
	if add > 0 {
		a.approvalTokens = min(32, a.approvalTokens+add)
		a.lastApprovalRefill = a.lastApprovalRefill.Add(time.Duration(add) * time.Minute)
	}
	counter := a.approvals[sessionID]
	if a.approvalTokens == 0 || counter.count >= 8 || (counter.count == 0 && len(a.approvals) >= 1024) {
		return false
	}
	a.approvalTokens--
	counter.count++
	counter.expires = expires
	a.approvals[sessionID] = counter
	return true
}

func (a *Authority) takeTerminalRequest(id string, expires time.Time, now time.Time) bool {
	if !now.Before(expires) {
		return false
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	for key, counter := range a.terminalRequests {
		if !now.Before(counter.expires) {
			delete(a.terminalRequests, key)
		}
	}
	counter := a.terminalRequests[id]
	if counter.count >= maxPolls || (counter.count == 0 && len(a.terminalRequests) >= maxPending) {
		return false
	}
	counter.count++
	counter.expires = expires
	a.terminalRequests[id] = counter
	return true
}

func (a *Authority) Start(browserResource string) (Start, error) {
	binding, ok := a.byBrowser[browserResource]
	if !ok || !a.takeStartToken(time.Now()) {
		return Start{}, ErrDenied
	}
	for attempt := 0; attempt < 8; attempt++ {
		deviceCode, err := codeGenerator(32)
		if err != nil {
			return Start{}, ErrUnavailable
		}
		userCode, err := codeGenerator(5)
		if err != nil || len(userCode) != 8 {
			return Start{}, ErrUnavailable
		}
		d, u := digest(deviceCode), digest(userCode)
		result := Start{DeviceCode: deviceCode, UserCode: userCode}
		collision := false
		err = a.state.Update(func(tx *store.Tx) error {
			if err := a.purge(tx); err != nil {
				return err
			}
			items, err := tx.List(transactionBucket, "device:", 4096)
			if err != nil {
				return ErrUnavailable
			}
			pending := 0
			for _, item := range items {
				var current record
				if json.Unmarshal(item.Value, &current) != nil || !a.validRecord(current) {
					return ErrUnavailable
				}
				if current.Status == "pending" || current.Status == "approved" {
					pending++
				}
			}
			if pending >= maxPending {
				return ErrDenied
			}
			id := recordID(d)
			var old record
			if err := tx.Get(transactionBucket, id, &old); !errors.Is(err, store.ErrMissing) {
				collision = true
				return nil
			}
			var existing string
			if err := tx.Get(transactionBucket, userID(u), &existing); !errors.Is(err, store.ErrMissing) {
				collision = true
				return nil
			}
			record := record{ID: id, DeviceDigest: d, UserDigest: u, BindingHash: binding.Hash, BrowserResource: binding.Browser.ID, APIResource: binding.API.ID, Methods: append([]string(nil), binding.Methods...), IssuedAt: tx.Now(), ExpiresAt: tx.Now().Add(Lifetime), Status: "pending"}
			if !a.validRecord(record) {
				return ErrUnavailable
			}
			result.ExpiresAt = record.ExpiresAt
			if tx.Put(transactionBucket, id, record) != nil || tx.Put(transactionBucket, userID(u), id) != nil {
				return ErrUnavailable
			}
			return nil
		})
		if err != nil {
			return Start{}, ErrDenied
		}
		if !collision {
			return result, nil
		}
	}
	return Start{}, ErrUnavailable
}

func (a *Authority) purge(tx *store.Tx) error {
	items, err := tx.List(transactionBucket, "device:", 4096)
	if err != nil {
		return ErrUnavailable
	}
	for _, item := range items {
		var record record
		if json.Unmarshal(item.Value, &record) != nil || !validStoredRecord(record) {
			return ErrUnavailable
		}
		if _, declared := a.byHash[record.BindingHash]; !declared {
			var indexed string
			err := tx.Get(transactionBucket, userID(record.UserDigest), &indexed)
			active := record.Status == "pending" || record.Status == "approved"
			if (active && (err != nil || indexed != record.ID)) || (!active && err != nil && !errors.Is(err, store.ErrMissing)) || tx.Delete(transactionBucket, item.ID) != nil {
				return ErrUnavailable
			}
			if err == nil && indexed == record.ID && tx.Delete(transactionBucket, userID(record.UserDigest)) != nil {
				return ErrUnavailable
			}
			continue
		}
		if !a.validRecord(record) {
			return ErrUnavailable
		}
		if !tx.Now().Before(record.ExpiresAt) && (record.Status == "pending" || record.Status == "approved") {
			var indexed string
			if tx.Get(transactionBucket, userID(record.UserDigest), &indexed) != nil || indexed != record.ID {
				return ErrUnavailable
			}
			record.Status = "expired"
			if tx.Put(transactionBucket, item.ID, record) != nil || tx.Delete(transactionBucket, userID(record.UserDigest)) != nil {
				return ErrUnavailable
			}
			continue
		}
		if (record.Status == "expired" || record.Status == "denied" || record.Status == "cancelled" || record.Status == "redeemed") && !tx.Now().Before(record.ExpiresAt.Add(terminalRetention)) {
			if tx.Delete(transactionBucket, item.ID) != nil {
				return ErrUnavailable
			}
			var indexed string
			err := tx.Get(transactionBucket, userID(record.UserDigest), &indexed)
			if err == nil && indexed == record.ID && tx.Delete(transactionBucket, userID(record.UserDigest)) != nil {
				return ErrUnavailable
			}
			if err != nil && !errors.Is(err, store.ErrMissing) {
				return ErrUnavailable
			}
		}
	}
	return nil
}

func (a *Authority) findByCode(tx *store.Tx, code string) (record, error) {
	d := digest(code)
	var record record
	if tx.Get(transactionBucket, recordID(d), &record) != nil || !a.validRecord(record) || record.ID != recordID(d) || record.DeviceDigest != d {
		return record, ErrDenied
	}
	return record, nil
}

func validStoredRecord(record record) bool {
	if record.ID != recordID(record.DeviceDigest) || record.UserDigest == ([32]byte{}) || !lowerHexDigest(record.BindingHash) || !config.ValidID(record.BrowserResource) || !config.ValidID(record.APIResource) || len(record.Methods) == 0 || record.IssuedAt.IsZero() || record.ExpiresAt.IsZero() || !record.ExpiresAt.After(record.IssuedAt) || record.Polls < 0 || record.Polls > maxPolls || record.Cancels < 0 || record.Cancels > 4 || record.Approvals < 0 || record.Approvals > maxApprovals || (!record.LastPoll.IsZero() && (record.LastPoll.Before(record.IssuedAt) || record.LastPoll.After(record.ExpiresAt))) {
		return false
	}
	methods := map[string]bool{}
	for _, method := range record.Methods {
		if !config.ValidMethod(method) || methods[method] {
			return false
		}
		methods[method] = true
	}
	unauthorized := record.HumanID == "" && record.HumanGeneration == 0 && record.APIPrincipal == "" && record.SessionExpiresAt.IsZero() && record.SessionID == "" && record.SessionGeneration == 0
	authorized := config.ValidHumanID(record.HumanID) && record.HumanGeneration != 0 && config.ValidID(record.APIPrincipal) && !record.SessionExpiresAt.IsZero() && !record.SessionExpiresAt.Before(record.IssuedAt) && lowerHexID(record.SessionID) && record.SessionGeneration != 0
	switch record.Status {
	case "pending":
		return unauthorized
	case "approved", "redeemed":
		return authorized
	case "denied", "cancelled", "expired":
		return unauthorized || authorized
	default:
		return false
	}
}

func (a *Authority) validRecord(record record) bool {
	if !validStoredRecord(record) {
		return false
	}
	binding, ok := a.byHash[record.BindingHash]
	if !ok || binding.Browser.ID != record.BrowserResource || binding.API.ID != record.APIResource || len(binding.Methods) != len(record.Methods) {
		return false
	}
	for i := range binding.Methods {
		if binding.Methods[i] != record.Methods[i] {
			return false
		}
	}
	if record.HumanID != "" && binding.Principals[record.HumanID] != record.APIPrincipal {
		return false
	}
	return true
}

func (a *Authority) Poll(deviceCode string) (Poll, error) {
	// Terminal records are observed read-only. They cannot produce another key,
	// and keeping those reads outside a Bolt write transaction prevents a lost
	// response from becoming an inexpensive write-amplification primitive.
	var observed record
	var observedNow time.Time
	if err := a.state.View(func(tx *store.Tx) error {
		var err error
		observedNow = tx.Now()
		observed, err = a.findByCode(tx, deviceCode)
		return err
	}); err != nil {
		return Poll{}, ErrDenied
	}
	if observed.Status != "pending" && observed.Status != "approved" {
		if !a.takeTerminalRequest(observed.ID, observed.ExpiresAt.Add(terminalRetention), observedNow) {
			return Poll{}, ErrDenied
		}
		return Poll{Status: observed.Status, ExpiresAt: observed.ExpiresAt}, nil
	}
	var record record
	redeeming := false
	err := a.state.Update(func(tx *store.Tx) error {
		if err := a.purge(tx); err != nil {
			return err
		}
		var err error
		record, err = a.findByCode(tx, deviceCode)
		if err != nil {
			return err
		}
		if record.Status != "pending" && record.Status != "approved" {
			return nil
		}
		if record.Status == "pending" {
			if record.Polls >= maxPolls {
				return ErrDenied
			}
			if !record.LastPoll.IsZero() && tx.Now().Sub(record.LastPoll) < PollInterval {
				return ErrSlowDown
			}
			record.Polls++
			record.LastPoll = tx.Now()
		}
		if record.Status == "approved" {
			record.Status = "redeemed"
			redeeming = true
		}
		if !a.validRecord(record) || tx.Put(transactionBucket, record.ID, record) != nil {
			return ErrUnavailable
		}
		return nil
	})
	if err != nil {
		return Poll{}, err
	}
	if record.Status != "redeemed" || !redeeming {
		return Poll{Status: record.Status, ExpiresAt: record.ExpiresAt}, nil
	}
	if a.sessions.CheckPrincipal(record.HumanID, record.HumanGeneration) != nil || a.sessions.CheckDeviceSession(record.SessionID, record.SessionGeneration, record.HumanID, record.HumanGeneration) != nil {
		return Poll{Status: "redeemed", ExpiresAt: record.ExpiresAt}, nil
	}
	binding, ok := a.byHash[record.BindingHash]
	if !ok || binding.Principals[record.HumanID] != record.APIPrincipal {
		return Poll{Status: "redeemed", ExpiresAt: record.ExpiresAt}, nil
	}
	var now time.Time
	if a.state.View(func(tx *store.Tx) error { now = tx.Now(); return nil }) != nil {
		return Poll{Status: "redeemed", ExpiresAt: record.ExpiresAt}, nil
	}
	lifetime := CredentialLife
	untilSession := record.SessionExpiresAt.Sub(now)
	if untilSession < lifetime {
		lifetime = untilSession
	}
	if lifetime < time.Minute {
		return Poll{Status: "redeemed", ExpiresAt: record.ExpiresAt}, nil
	}
	secret, key, err := a.keys.IssueDevice(record.APIPrincipal, []access.Grant{{Resource: record.APIResource, Methods: record.Methods}}, lifetime, access.DeviceCredential{HumanID: record.HumanID, HumanGeneration: record.HumanGeneration, MappingHash: record.BindingHash, Session: record.SessionID, SessionGeneration: record.SessionGeneration})
	if err != nil {
		return Poll{Status: "redeemed", ExpiresAt: record.ExpiresAt}, nil
	}
	return Poll{Status: "approved", Secret: secret, ExpiresAt: key.ExpiresAt}, nil
}

func (a *Authority) Approve(admitted session.Admission, userCode string, allow bool) (Binding, error) {
	if admitted.SessionID == "" || admitted.Principal == "" || admitted.PrincipalGeneration == 0 || admitted.ExpiresAt.IsZero() || a.sessions.Check(admitted) != nil {
		return Binding{}, ErrDenied
	}
	if !a.takeApprovalToken(admitted.SessionID, admitted.ExpiresAt, time.Now()) {
		return Binding{}, ErrDenied
	}
	var binding Binding
	err := a.state.Update(func(tx *store.Tx) error {
		if err := a.purge(tx); err != nil {
			return err
		}
		index := userID(digest(userCode))
		var id string
		if tx.Get(transactionBucket, index, &id) != nil {
			return ErrDenied
		}
		var record record
		if tx.Get(transactionBucket, id, &record) != nil || !a.validRecord(record) || record.UserDigest != digest(userCode) || record.Status != "pending" || record.Approvals >= maxApprovals || !tx.Now().Before(record.ExpiresAt) || record.BrowserResource != admitted.Resource {
			return ErrDenied
		}
		binding = a.byHash[record.BindingHash]
		if binding.Hash == "" || binding.Browser.Host != admitted.Host {
			return ErrDenied
		}
		record.Approvals++
		if !allow {
			record.Status = "denied"
			return tx.Put(transactionBucket, id, record)
		}
		principal := binding.Principals[admitted.Principal]
		if principal == "" {
			return ErrDenied
		}
		record.Status, record.HumanID, record.HumanGeneration, record.APIPrincipal, record.SessionExpiresAt, record.SessionID, record.SessionGeneration = "approved", admitted.Principal, admitted.PrincipalGeneration, principal, admitted.ExpiresAt, admitted.SessionID, admitted.SessionGeneration
		return tx.Put(transactionBucket, id, record)
	})
	if err != nil {
		return Binding{}, ErrDenied
	}
	return binding, nil
}

func (a *Authority) Cancel(deviceCode string) error {
	var observed record
	var observedNow time.Time
	if err := a.state.View(func(tx *store.Tx) error {
		var err error
		observedNow = tx.Now()
		observed, err = a.findByCode(tx, deviceCode)
		return err
	}); err != nil {
		return ErrDenied
	}
	if observed.Status != "pending" && observed.Status != "approved" {
		if !a.takeTerminalRequest(observed.ID, observed.ExpiresAt.Add(terminalRetention), observedNow) {
			return ErrDenied
		}
		return ErrDenied
	}
	return a.state.Update(func(tx *store.Tx) error {
		if err := a.purge(tx); err != nil {
			return err
		}
		record, err := a.findByCode(tx, deviceCode)
		if err != nil || !a.validRecord(record) || record.Cancels >= 4 || (record.Status != "pending" && record.Status != "approved") {
			return ErrDenied
		}
		record.Cancels++
		record.Status = "cancelled"
		return tx.Put(transactionBucket, record.ID, record)
	})
}

func (a *Authority) checkCredential(device access.DeviceCredential, principal, resource, method string, principalGeneration uint64) error {
	binding, ok := a.byHash[device.MappingHash]
	if !ok || binding.Principals[device.HumanID] != principal || binding.API.ID != resource || !contains(binding.Methods, method) || device.HumanGeneration == 0 || principalGeneration == 0 {
		return ErrDenied
	}
	if a.sessions.CheckPrincipal(device.HumanID, device.HumanGeneration) != nil {
		return ErrDenied
	}
	return a.sessions.CheckDeviceSession(device.Session, device.SessionGeneration, device.HumanID, device.HumanGeneration)
}

func lowerHexDigest(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, c := range value {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return false
		}
	}
	return true
}

func lowerHexID(value string) bool {
	if len(value) != 32 {
		return false
	}
	for _, c := range value {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return false
		}
	}
	return true
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
