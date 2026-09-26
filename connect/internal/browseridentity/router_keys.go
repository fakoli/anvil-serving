package browseridentity

import (
	"net/http"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

// SignRouterKeys delegates a freshly checked Home session to one fixed broker
// audience. The caller must derive operator from its explicit operator policy,
// and fence from the account authority, never from browser input.
func (s *Signer) SignRouterKeys(admitted session.Admission, request *http.Request, operator bool, fence string) (string, error) {
	if admitted.Resource != "_connect-home" || !lowerHex(fence, 32) || request == nil || request.URL == nil || request.Method != "POST" || request.URL.RequestURI() != "/v1/connect/keys" || request.Host != admitted.Host {
		return "", ErrDenied
	}
	delegated := admitted
	delegated.Resource, delegated.Epoch, delegated.ApplicationRole = "router-keys", fence, "member"
	if operator {
		delegated.ApplicationRole = "admin"
	}
	return s.Sign(delegated, config.Rule{ID: "router-keys", Host: admitted.Host, PathPrefix: "/v1/connect/keys", Methods: []string{"POST"}}, request)
}
