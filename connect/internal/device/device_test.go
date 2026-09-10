package device

import (
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

type humanFixture struct {
	denied        bool
	sessionDenied bool
	generation    uint64
}

func (h *humanFixture) Check(admitted session.Admission) error {
	if h.denied || admitted.PrincipalGeneration != h.generation {
		return session.ErrDenied
	}
	return nil
}
func (h *humanFixture) CheckPrincipal(_ string, generation uint64) error {
	if h.denied || generation != h.generation {
		return session.ErrDenied
	}
	return nil
}
func (h *humanFixture) CheckDeviceSession(_ string, _ uint64, _ string, generation uint64) error {
	if h.denied || h.sessionDenied || generation != h.generation {
		return session.ErrDenied
	}
	return nil
}

func deviceGateway() config.Gateway {
	limits := config.Limits{RequestBytes: 4096, Concurrent: 2, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 60}
	human := "human:" + strings.Repeat("a", 64)
	return config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:18080", MaxConcurrent: 4, Resources: []config.Resource{
		{Rule: config.Rule{ID: "dash", Host: "dash.example.test", PathPrefix: "/app", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none", Limits: limits}, Connector: "dash-origin", TunnelAddress: "127.0.0.1:18081"},
		{Rule: config.Rule{ID: "router", Host: "api.example.test", PathPrefix: "/v1", Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer", Limits: limits}, Connector: "api-origin", TunnelAddress: "127.0.0.1:18082"},
	}, DeviceAuthorizations: []config.DeviceAuthorization{{BrowserResource: "dash", APIResource: "router", Methods: []string{"POST"}, Label: "Synthetic device", Principals: map[string]string{human: "owner"}}}}
}

func fixture(t *testing.T) (*Authority, *access.Keys, *humanFixture, *time.Time, session.Admission) {
	t.Helper()
	now := time.Date(2026, 9, 10, 1, 2, 3, 0, time.UTC)
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = state.Close() })
	gateway := deviceGateway()
	keys, err := access.NewKeys(state, []config.Rule{gateway.Resources[1].Rule})
	if err != nil {
		t.Fatal(err)
	}
	if err := keys.SetPrincipal("owner", []access.Grant{{Resource: "router", Methods: []string{"POST"}}}, false); err != nil {
		t.Fatal(err)
	}
	humans := &humanFixture{generation: 1}
	authority, err := New(state, gateway, humans, keys)
	if err != nil {
		t.Fatal(err)
	}
	admitted := session.Admission{SessionID: strings.Repeat("1", 32), SessionGeneration: 1, Principal: "human:" + strings.Repeat("a", 64), PrincipalGeneration: 1, Resource: "dash", Host: "dash.example.test", Epoch: strings.Repeat("b", 64), ExpiresAt: now.Add(2 * time.Hour)}
	return authority, keys, humans, &now, admitted
}

func TestApprovalRedeemsOnceAndLogoutRevokesAPIAdmission(t *testing.T) {
	authority, keys, humans, _, admitted := fixture(t)
	started, err := authority.Start("dash")
	if err != nil || len(started.DeviceCode) < 43 || len(started.UserCode) != 8 {
		t.Fatal("device start did not mint bounded opaque codes")
	}
	if _, err := authority.Approve(admitted, started.UserCode, true); err != nil {
		t.Fatal("mapped human approval denied")
	}
	polled, err := authority.Poll(started.DeviceCode)
	if err != nil || polled.Status != "approved" || polled.Secret == "" {
		t.Fatal("approved request did not issue exactly one credential")
	}
	if again, err := authority.Poll(started.DeviceCode); err != nil || again.Secret != "" || again.Status != "redeemed" {
		t.Fatal("lost redemption response could be retried")
	}
	api, err := keys.Authenticate(polled.Secret, "router", "POST")
	if err != nil || keys.Check(api) != nil {
		t.Fatal("device credential did not admit before revocation")
	}
	humans.sessionDenied = true
	if _, err := keys.Authenticate(polled.Secret, "router", "POST"); err == nil || keys.Check(api) == nil {
		t.Fatal("approving browser session revoke did not revoke device credential")
	}
	humans.sessionDenied = false
	humans.generation++
	if _, err := keys.Authenticate(polled.Secret, "router", "POST"); err == nil || keys.Check(api) == nil {
		t.Fatal("human generation change did not revoke device credential")
	}
}

func TestMappingDriftInvalidatesExistingDeviceAdmission(t *testing.T) {
	authority, keys, _, _, admitted := fixture(t)
	started, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := authority.Approve(admitted, started.UserCode, true); err != nil {
		t.Fatal(err)
	}
	polled, err := authority.Poll(started.DeviceCode)
	if err != nil || polled.Secret == "" {
		t.Fatal("device credential unavailable")
	}
	api, err := keys.Authenticate(polled.Secret, "router", "POST")
	if err != nil {
		t.Fatal(err)
	}
	authority.byHash = map[string]Binding{}
	if _, err := keys.Authenticate(polled.Secret, "router", "POST"); err == nil || keys.Check(api) == nil {
		t.Fatal("mapping drift did not revoke device credential")
	}
}

func TestDevicePollRateDenyAndCancelAreTerminal(t *testing.T) {
	authority, _, _, now, admitted := fixture(t)
	started, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := authority.Poll(started.DeviceCode); err != nil {
		t.Fatal("first pending poll denied")
	}
	if _, err := authority.Poll(started.DeviceCode); !errors.Is(err, ErrSlowDown) {
		t.Fatal("rapid poll did not slow down")
	}
	if _, err := authority.Approve(admitted, started.UserCode, false); err != nil {
		t.Fatal("explicit denial failed")
	}
	if result, err := authority.Poll(started.DeviceCode); err != nil || result.Status != "denied" || result.Secret != "" {
		t.Fatal("denied request issued a credential")
	}
	started, err = authority.Start("dash")
	if err != nil || authority.Cancel(started.DeviceCode) != nil {
		t.Fatal("cancel did not record pending request")
	}
	if _, err := authority.Approve(admitted, started.UserCode, true); err == nil {
		t.Fatal("cancelled request was approved")
	}
	started, err = authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	*now = started.ExpiresAt
	if expired, err := authority.Poll(started.DeviceCode); err != nil || expired.Status != "expired" || expired.Secret != "" {
		t.Fatal("expired request did not remain terminal without a credential")
	}
}

func TestBindingAndCSRFAreExact(t *testing.T) {
	authority, _, _, _, admitted := fixture(t)
	binding, ok := authority.Binding("dash")
	path, pathOK := authority.Path("dash")
	if !ok || !pathOK || path != "/app/_anvil-connect/device" || !authority.ValidCSRF(admitted, binding, authority.CSRF(admitted, binding)) || authority.ValidCSRF(admitted, binding, strings.Repeat("0", 64)) {
		t.Fatal("device binding path or csrf boundary widened")
	}
}

func TestStartRetriesLiveUserCodeCollisionAndRejectsCorruptRecord(t *testing.T) {
	authority, _, _, _, _ := fixture(t)
	collisionUser := "ABCDEFGH"
	if err := authority.state.Update(func(tx *store.Tx) error {
		return tx.Put(transactionBucket, userID(digest(collisionUser)), "device:"+strings.Repeat("a", 64))
	}); err != nil {
		t.Fatal(err)
	}
	values := []string{strings.Repeat("A", 52), collisionUser, strings.Repeat("B", 52), "IJKLMNOP"}
	previous := codeGenerator
	codeGenerator = func(size int) (string, error) {
		if len(values) == 0 {
			return "", ErrUnavailable
		}
		value := values[0]
		values = values[1:]
		if len(value) == 0 || (size == 32 && len(value) != 52) || (size == 5 && len(value) != 8) {
			return "", ErrUnavailable
		}
		return value, nil
	}
	defer func() { codeGenerator = previous }()
	started, err := authority.Start("dash")
	if err != nil || started.UserCode != "IJKLMNOP" || len(values) != 0 {
		t.Fatalf("collision retry start=%+v err=%v remaining=%d", started, err, len(values))
	}
	if err := authority.state.Update(func(tx *store.Tx) error {
		var current record
		if err := tx.Get(transactionBucket, recordID(digest(started.DeviceCode)), &current); err != nil {
			return err
		}
		current.Methods = []string{"GET"}
		return tx.Put(transactionBucket, current.ID, current)
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := authority.Poll(started.DeviceCode); err == nil {
		t.Fatal("corrupt persisted method scope was accepted")
	}
}

func TestCancelWinsAgainstApprovedUnredeemedRequest(t *testing.T) {
	authority, _, _, _, admitted := fixture(t)
	started, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := authority.Approve(admitted, started.UserCode, true); err != nil {
		t.Fatal(err)
	}
	if err := authority.Cancel(started.DeviceCode); err != nil {
		t.Fatal("cancel lost approved race", err)
	}
	if result, err := authority.Poll(started.DeviceCode); err != nil || result.Status != "cancelled" || result.Secret != "" {
		t.Fatalf("cancelled approved request result=%+v err=%v", result, err)
	}
}

func TestApproveRejectsCorruptUserIndexWithoutMutation(t *testing.T) {
	authority, _, _, _, admitted := fixture(t)
	first, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	second, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	if err := authority.state.Update(func(tx *store.Tx) error {
		return tx.Put(transactionBucket, userID(digest(first.UserCode)), recordID(digest(second.DeviceCode)))
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := authority.Approve(admitted, first.UserCode, true); err == nil {
		t.Fatal("corrupt user index approved another device")
	}
	if err := authority.state.View(func(tx *store.Tx) error {
		var current record
		if err := tx.Get(transactionBucket, recordID(digest(second.DeviceCode)), &current); err != nil {
			return err
		}
		if current.Status != "pending" || current.HumanID != "" {
			t.Fatal("corrupt approval mutated target record")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestTerminalRequestPastRetentionIsRejected(t *testing.T) {
	authority, _, _, now, _ := fixture(t)
	started, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	if err := authority.Cancel(started.DeviceCode); err != nil {
		t.Fatal(err)
	}
	*now = started.ExpiresAt.Add(terminalRetention)
	if _, err := authority.Poll(started.DeviceCode); err == nil {
		t.Fatal("out-of-retention terminal poll accepted")
	}
	if err := authority.Cancel(started.DeviceCode); err == nil {
		t.Fatal("out-of-retention terminal cancel accepted")
	}
}

func TestBindingDriftPurgesOldTransactionsAndRevokesOldCredential(t *testing.T) {
	authority, keys, _, _, admitted := fixture(t)
	pending, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	approved, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := authority.Approve(admitted, approved.UserCode, true); err != nil {
		t.Fatal(err)
	}
	issued, err := authority.Poll(approved.DeviceCode)
	if err != nil || issued.Secret == "" {
		t.Fatal("fixture device key unavailable", err)
	}
	binding, ok := authority.Binding("dash")
	if !ok {
		t.Fatal("missing binding")
	}
	binding.Label = "Changed synthetic device"
	binding.Hash = bindingHash(binding)
	authority.byBrowser = map[string]Binding{"dash": binding}
	authority.byHash = map[string]Binding{binding.Hash: binding}
	fresh, err := authority.Start("dash")
	if err != nil || fresh.DeviceCode == "" {
		t.Fatal("drift did not purge old records and allow fresh start", err)
	}
	if _, err := authority.Poll(pending.DeviceCode); err == nil {
		t.Fatal("old pending device remained usable after binding drift")
	}
	if _, err := keys.Authenticate(issued.Secret, "router", "POST"); err == nil {
		t.Fatal("old device key survived binding drift")
	}
}

func TestBindingDriftPurgesExpiredRecordWithoutUserIndex(t *testing.T) {
	authority, _, _, now, _ := fixture(t)
	old, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	*now = old.ExpiresAt
	if result, err := authority.Poll(old.DeviceCode); err != nil || result.Status != "expired" {
		t.Fatalf("expire result=%+v err=%v", result, err)
	}
	binding, _ := authority.Binding("dash")
	binding.Label = "Reloaded device"
	binding.Hash = bindingHash(binding)
	authority.byBrowser = map[string]Binding{"dash": binding}
	authority.byHash = map[string]Binding{binding.Hash: binding}
	if fresh, err := authority.Start("dash"); err != nil || fresh.DeviceCode == "" {
		t.Fatal("expired stale record blocked fresh start", err)
	}
}

func TestRevokedApprovingSessionCannotRedeem(t *testing.T) {
	authority, keys, humans, _, admitted := fixture(t)
	started, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := authority.Approve(admitted, started.UserCode, true); err != nil {
		t.Fatal(err)
	}
	humans.sessionDenied = true
	result, err := authority.Poll(started.DeviceCode)
	if err != nil || result.Status != "redeemed" || result.Secret != "" {
		t.Fatalf("revoked source session redeemed result=%+v err=%v", result, err)
	}
	if _, err := keys.Authenticate(result.Secret, "router", "POST"); err == nil {
		t.Fatal("empty device credential admitted")
	}
}

func TestTerminalCleanupDoesNotDeleteReusedUserCodeIndex(t *testing.T) {
	authority, _, _, now, _ := fixture(t)
	values := []string{strings.Repeat("A", 52), "ABCDEFGH", strings.Repeat("B", 52), "ABCDEFGH", strings.Repeat("C", 52), "IJKLMNOP"}
	previous := codeGenerator
	codeGenerator = func(size int) (string, error) { value := values[0]; values = values[1:]; return value, nil }
	defer func() { codeGenerator = previous }()
	old, err := authority.Start("dash")
	if err != nil {
		t.Fatal(err)
	}
	*now = old.ExpiresAt
	if _, err := authority.Poll(old.DeviceCode); err != nil {
		t.Fatal(err)
	}
	fresh, err := authority.Start("dash")
	if err != nil || fresh.UserCode != old.UserCode {
		t.Fatal("same user code not safely reused", err)
	}
	*now = old.ExpiresAt.Add(terminalRetention)
	if _, err := authority.Start("dash"); err != nil {
		t.Fatal(err)
	}
	if err := authority.state.View(func(tx *store.Tx) error {
		var indexed string
		if err := tx.Get(transactionBucket, userID(digest(fresh.UserCode)), &indexed); err != nil {
			return err
		}
		if indexed != recordID(digest(fresh.DeviceCode)) {
			t.Fatal("old cleanup deleted reused user-code index")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestPurgeUnknownTerminalBindingPreservesDifferentUserIndex(t *testing.T) {
	authority, _, _, now, _ := fixture(t)
	deviceDigest, userDigest := digest("old-device"), digest("old-user")
	stale := record{ID: recordID(deviceDigest), DeviceDigest: deviceDigest, UserDigest: userDigest, BindingHash: strings.Repeat("f", 64), BrowserResource: "dash", APIResource: "router", Methods: []string{"POST"}, IssuedAt: now.Add(-2 * Lifetime), ExpiresAt: now.Add(-Lifetime), Status: "expired"}
	other := "device:" + strings.Repeat("a", 64)
	if err := authority.state.Update(func(tx *store.Tx) error {
		if tx.Put(transactionBucket, stale.ID, stale) != nil {
			return ErrUnavailable
		}
		return tx.Put(transactionBucket, userID(userDigest), other)
	}); err != nil {
		t.Fatal(err)
	}
	if err := authority.state.Update(func(tx *store.Tx) error { return authority.purge(tx) }); err != nil {
		t.Fatal(err)
	}
	if err := authority.state.View(func(tx *store.Tx) error {
		var got string
		if err := tx.Get(transactionBucket, stale.ID, &got); !errors.Is(err, store.ErrMissing) {
			t.Fatal("stale record retained")
		}
		if err := tx.Get(transactionBucket, userID(userDigest), &got); err != nil || got != other {
			t.Fatal("different index removed")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
