package control

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"mime"
	"net/http"
	"sync"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/credential"
	"github.com/fakoli/anvil-serving/connect/internal/httpedge"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
	"github.com/fakoli/anvil-serving/connect/internal/tunnelgate"
)

const ChallengePath = "/v1/challenge"
const EnrollmentPath = "/v1/enroll"
const RenewalPath = "/v1/renew"
const RotationPath = "/v1/rotate"
const challengeLifetime = 30 * time.Second

type pending struct {
	request       ChallengeRequest
	issued, until time.Time
}

type Server struct {
	host       string
	identity   *identity.Manager
	issuer     *credential.Issuer
	leases     *tunnelgate.Leases
	routes     map[string]config.Resource
	now        func() time.Time
	slots      chan struct{}
	mu         sync.Mutex
	challenges map[string]pending
	closed     bool
}

func NewServer(host string, gateway config.Gateway, manager *identity.Manager, issuer *credential.Issuer, leases *tunnelgate.Leases, now func() time.Time) (*Server, error) {
	if !config.ValidHost(host) || gateway.Validate() != nil || manager == nil || issuer == nil || leases == nil {
		return nil, ErrDenied
	}
	if now == nil {
		now = time.Now
	}
	s := &Server{host: host, identity: manager, issuer: issuer, leases: leases, now: now, slots: make(chan struct{}, min(16, gateway.MaxConcurrent)), challenges: map[string]pending{}, routes: map[string]config.Resource{}}
	for _, resource := range gateway.Resources {
		if resource.Rule.Host == host {
			return nil, ErrDenied
		}
		resource.Rule.Methods = append([]string(nil), resource.Rule.Methods...)
		s.routes[resource.Rule.ID] = resource
	}
	return s, nil
}

func (s *Server) Close() {
	s.mu.Lock()
	s.closed = true
	s.challenges = map[string]pending{}
	s.mu.Unlock()
}

func (s *Server) scope(id, resource string) bool {
	route, ok := s.routes[resource]
	return ok && config.ValidID(id) && route.Connector == id
}

func (s *Server) challenge(request ChallengeRequest) (ChallengeResponse, error) {
	if !s.scope(request.Installation, request.Resource) || (request.Purpose != "renew" && request.Purpose != "rotate") || !validDigest(request.Digest) {
		return ChallengeResponse{}, ErrDenied
	}
	if s.identity.AuthorizeChallenge(request.Proof, request.Installation, request.Resource, request.Digest) != nil {
		return ChallengeResponse{}, ErrDenied
	}
	request.Proof = "" // do not retain credential material in pending challenges
	var nonce [32]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return ChallengeResponse{}, ErrDenied
	}
	value := hex.EncodeToString(nonce[:])
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	count := 0
	for key, pending := range s.challenges {
		if now.Before(pending.issued) || !now.Before(pending.until) {
			delete(s.challenges, key)
			continue
		}
		if pending.request.Installation == request.Installation && pending.request.Resource == request.Resource {
			count++
		}
	}
	if s.closed || len(s.challenges) >= 256 || count >= 4 {
		return ChallengeResponse{}, ErrDenied
	}
	if _, exists := s.challenges[value]; exists {
		return ChallengeResponse{}, ErrDenied
	}
	s.challenges[value] = pending{request, now, now.Add(challengeLifetime)}
	return ChallengeResponse{Schema, value}, nil
}

func (s *Server) consume(nonce string, request ChallengeRequest) bool {
	if !validDigest(nonce) {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	value, exists := s.challenges[nonce]
	if !exists || s.closed || value.request != request {
		return false
	}
	delete(s.challenges, nonce) // burn before signature verification or side effects
	now := s.now()
	return !now.Before(value.issued) && now.Before(value.until)
}

func reply(w http.ResponseWriter, value any) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_ = json.NewEncoder(w).Encode(value)
}

func failure(w http.ResponseWriter, status int) {
	w.Header().Set("Cache-Control", "no-store")
	http.Error(w, http.StatusText(status), status)
}

func installationReply(i identity.Installation) InstallationResponse {
	return InstallationResponse{Schema, i.ID, i.Role, i.Status, i.Epoch, i.Generation, i.Fingerprint}
}

// ServeHTTP is mounted only at the managed TLS edge's owned control socket.
// The outer public TLS protects invitations and proofs in transit; no browser
// cookie, API credential or forwarded identity can authorize an operation.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if httpedge.ValidateHead(r) != nil || r.Host != s.host || r.Method != "POST" || r.URL.RawQuery != "" || r.URL.ForceQuery || r.ContentLength > maxBody {
		failure(w, 400)
		return
	}
	for _, name := range []string{"Origin", "Cookie", "Authorization", "Proxy-Authorization", "X-Api-Key", "Upgrade"} {
		if len(r.Header.Values(name)) > 0 {
			failure(w, 400)
			return
		}
	}
	if len(r.Header.Values("Content-Type")) != 1 {
		failure(w, 400)
		return
	}
	media, parameters, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || media != "application/json" || len(parameters) > 1 || (len(parameters) == 1 && parameters["charset"] != "utf-8") {
		failure(w, 400)
		return
	}
	s.mu.Lock()
	closed := s.closed
	s.mu.Unlock()
	if closed {
		failure(w, 503)
		return
	}
	select {
	case s.slots <- struct{}{}:
	default:
		failure(w, 429)
		return
	}
	defer func() { <-s.slots }()
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	r.Body = relay.Body(w, http.MaxBytesReader(w, r.Body, maxBody), ctx, 5*time.Second)
	defer r.Body.Close()
	switch r.URL.Path {
	case ChallengePath:
		var request ChallengeRequest
		if decode(r.Body, &request) != nil {
			failure(w, 400)
			return
		}
		response, err := s.challenge(request)
		if err != nil {
			failure(w, 403)
			return
		}
		reply(w, response)
	case EnrollmentPath:
		var request EnrollmentRequest
		if decode(r.Body, &request) != nil {
			failure(w, 400)
			return
		}
		key, err := decoded(request.PublicKey, 4096)
		if err != nil {
			failure(w, 403)
			return
		}
		installation, err := s.identity.Enroll(request.Invitation, key, request.Proof)
		if err != nil {
			failure(w, 403)
			return
		}
		reply(w, installationReply(installation))
	case RenewalPath:
		var request RenewalRequest
		if decode(r.Body, &request) != nil {
			failure(w, 400)
			return
		}
		csr, err := decoded(request.CSR, 16384)
		if err != nil || !s.consume(request.Nonce, ChallengeRequest{Installation: request.Installation, Resource: request.Resource, Purpose: "renew", Digest: RenewalDigest(csr)}) {
			failure(w, 403)
			return
		}
		verified, err := s.identity.Verify(request.Proof, "connector", request.Resource, request.Nonce)
		if err != nil || verified.ID != request.Installation {
			failure(w, 403)
			return
		}
		var certificate []byte
		if len(csr) > 0 {
			issued, err := s.issuer.Issue(verified, request.Resource, csr)
			if err != nil {
				failure(w, 403)
				return
			}
			certificate = issued.DER
		}
		token, _, err := s.leases.Issue(verified, request.Resource)
		if err != nil {
			failure(w, 403)
			return
		}
		lifetime, err := s.identity.PermissionLifetime(verified, request.Resource, access.ConnectorLeaseLifetime)
		if err != nil || lifetime < time.Millisecond {
			failure(w, 403)
			return
		}
		reply(w, RenewalResponse{Schema, verified.ID, request.Resource, verified.Epoch, verified.Generation, lifetime.Milliseconds(), token, encoded(certificate)})
	case RotationPath:
		var request RotationRequest
		if decode(r.Body, &request) != nil {
			failure(w, 400)
			return
		}
		key, err := decoded(request.PublicKey, 4096)
		if err != nil || request.OverlapMilliseconds < 0 || request.OverlapMilliseconds > identity.MaximumRotationOverlap.Milliseconds() || !s.consume(request.Nonce, ChallengeRequest{Installation: request.Installation, Resource: request.Resource, Purpose: "rotate", Digest: RotationDigest(key, request.OverlapMilliseconds)}) {
			failure(w, 403)
			return
		}
		rotated, err := s.identity.Rotate(request.Installation, key, request.OldProof, request.NewProof, request.Nonce, time.Duration(request.OverlapMilliseconds)*time.Millisecond)
		if err != nil {
			failure(w, 403)
			return
		}
		reply(w, installationReply(rotated))
	default:
		failure(w, 404)
	}
}
