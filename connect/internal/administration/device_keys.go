package administration

import (
	"regexp"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

var digestPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

// deviceKeyStatus is shared by inventory and individual revocation. Structural
// corruption fails closed; an intact credential whose authority changed is
// reported as invalidated rather than currently usable.
func (a *Authority) deviceKeyStatus(tx *store.Tx, key access.Key) (string, error) {
	if !objectIDPattern.MatchString(key.ID) || key.Generation == 0 || !config.ValidID(key.Principal) || key.PrincipalGeneration == 0 || !digestPattern.MatchString(key.Epoch) || key.Digest == ([32]byte{}) || !config.ValidHumanID(key.DeviceHuman) || key.DeviceHumanGeneration == 0 || !digestPattern.MatchString(key.DeviceMappingHash) || !objectIDPattern.MatchString(key.DeviceSession) || key.DeviceSessionGeneration == 0 || !validTimes(key.IssuedAt, key.ExpiresAt) || key.ExpiresAt.Sub(key.IssuedAt) > time.Hour || len(key.Grants) != 1 || !config.ValidID(key.Grants[0].Resource) || len(key.Grants[0].Methods) < 1 || len(key.Grants[0].Methods) > 7 {
		return "", ErrUnavailable
	}
	seen := map[string]bool{}
	for _, method := range key.Grants[0].Methods {
		if !config.ValidMethod(method) || seen[method] {
			return "", ErrUnavailable
		}
		seen[method] = true
	}
	status := issuedStatus(key.Revoked, key.ExpiresAt, tx.Now())
	if status != "issued" {
		return status, nil
	}
	binding, configured := a.bindings[key.DeviceMappingHash]
	rule, ruleConfigured := a.rules[key.Grants[0].Resource]
	if !configured || !ruleConfigured || rule.Access != "api" || binding.API.ID != rule.ID || binding.Principals[key.DeviceHuman] != key.Principal || key.Epoch != tx.Epoch() || tx.Now().Before(key.IssuedAt) {
		return "invalidated", nil
	}
	var principal access.Principal
	if tx.Get("principals", key.Principal, &principal) != nil || principal.ID != key.Principal || principal.Generation != key.PrincipalGeneration || principal.Disabled {
		return "invalidated", nil
	}
	for _, method := range key.Grants[0].Methods {
		allowed := false
		for _, candidate := range binding.Methods {
			if candidate == method {
				allowed = true
			}
		}
		if !allowed || !rule.Allows(rule.Host, rule.PathPrefix, method) || !grantPermits(principal.Grants, rule.ID, method) {
			return "invalidated", nil
		}
	}
	var source session.Session
	if tx.Get("sessions", "session:"+key.DeviceSession, &source) != nil || source.ID != key.DeviceSession || source.Generation != key.DeviceSessionGeneration || source.Principal != key.DeviceHuman || source.PrincipalGeneration != key.DeviceHumanGeneration || source.Resource != binding.Browser.ID || key.ExpiresAt.After(source.ExpiresAt) {
		return "invalidated", nil
	}
	admitted := session.Admission{SessionID: source.ID, SessionGeneration: source.Generation, Principal: source.Principal, PrincipalGeneration: source.PrincipalGeneration, Resource: source.Resource, Host: source.Host, Epoch: source.Epoch, ExpiresAt: source.ExpiresAt}
	if a.sessions.CheckTx(tx, admitted) != nil {
		return "invalidated", nil
	}
	return "issued", nil
}
func grantPermits(grants []access.Grant, resource, method string) bool {
	for _, grant := range grants {
		if grant.Resource == resource {
			for _, candidate := range grant.Methods {
				if candidate == method {
					return true
				}
			}
		}
	}
	return false
}
