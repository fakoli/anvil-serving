package control

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
)

var ErrClient = errors.New("installation control client denied")

const controlExchangeTimeout = 5 * time.Second

// ClientOptions permits an explicitly owned CA pool for a private deployment
// or synthetic test. A nil pool uses the operating system trust roots. It
// intentionally does not expose proxy-from-environment or transport hooks.
type ClientOptions struct {
	RootCAs      *x509.CertPool
	HTTPProxyURL string
}

// Client owns a fixed HTTPS control endpoint and one immutable local
// installation key. It never updates either after a rotation response.
type Client struct {
	gateway      string
	installation identity.Installation
	private      ed25519.PrivateKey
	transport    *http.Transport
	client       *http.Client
}

// NewClient creates an HTTPS-only control client. gatewayURL is exactly an
// https DNS origin with no port, path, query, fragment, or user information.
func NewClient(gatewayURL string, installation identity.Installation, private ed25519.PrivateKey, options ClientOptions) (*Client, error) {
	return newClient(gatewayURL, installation, private, options, nil)
}

// newClient allows a DNS-to-loopback fixture dialer only inside this package's
// tests. Production callers use NewClient and cannot select their own dialer.
func newClient(gatewayURL string, installation identity.Installation, private ed25519.PrivateKey, options ClientOptions, dial func(context.Context, string, string) (net.Conn, error)) (*Client, error) {
	u, err := controlOrigin(gatewayURL)
	if err != nil || !validInstallation(installation, private) {
		return nil, ErrClient
	}
	proxy, err := controlProxy(options.HTTPProxyURL)
	if err != nil {
		return nil, ErrClient
	}
	roots := options.RootCAs
	if roots != nil {
		roots = roots.Clone()
	}
	transport := &http.Transport{
		Proxy:                  proxy,
		DisableCompression:     true,
		DisableKeepAlives:      true,
		MaxConnsPerHost:        1,
		MaxIdleConnsPerHost:    1,
		MaxResponseHeaderBytes: maxBody,
		TLSHandshakeTimeout:    controlExchangeTimeout,
		ResponseHeaderTimeout:  controlExchangeTimeout,
		IdleConnTimeout:        controlExchangeTimeout,
		TLSClientConfig: &tls.Config{
			MinVersion: tls.VersionTLS13,
			ServerName: u.Host,
			RootCAs:    roots,
		},
	}
	transport.Protocols = new(http.Protocols)
	transport.Protocols.SetHTTP2(true)
	if dial != nil {
		transport.DialContext = dial
	}
	return &Client{
		gateway:      gatewayURL,
		installation: copyInstallation(installation),
		private:      append(ed25519.PrivateKey(nil), private...),
		transport:    transport,
		client: &http.Client{Transport: transport, CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		}},
	}, nil
}

func controlOrigin(raw string) (*url.URL, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Host == "" || u.Host != u.Hostname() || !config.ValidHost(u.Host) || u.Path != "" || u.RawPath != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || strings.ContainsAny(raw, "?#\\\r\n\t ") {
		return nil, ErrClient
	}
	return u, nil
}

func controlProxy(raw string) (func(*http.Request) (*url.URL, error), error) {
	if raw == "" {
		return nil, nil
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "http" || u.User != nil || u.Host == "" || u.Path != "" || u.RawPath != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || strings.ContainsAny(raw, "?#\\\r\n\t ") {
		return nil, ErrClient
	}
	return http.ProxyURL(u), nil
}

func validInstallation(installation identity.Installation, private ed25519.PrivateKey) bool {
	if !config.ValidID(installation.ID) || installation.Role != "connector" || len(installation.Epoch) != 64 || installation.Generation == 0 || len(installation.Resources) < 1 || len(installation.Resources) > 64 || len(private) != ed25519.PrivateKeySize {
		return false
	}
	epoch, err := hex.DecodeString(installation.Epoch)
	if err != nil || len(epoch) != 32 || hex.EncodeToString(epoch) != installation.Epoch {
		return false
	}
	resources := map[string]bool{}
	for _, resource := range installation.Resources {
		if !config.ValidID(resource) || resources[resource] {
			return false
		}
		resources[resource] = true
	}
	public, fingerprint, err := identity.PublicKey(private)
	return err == nil && subtle.ConstantTimeCompare(public, installation.PublicKey) == 1 && subtle.ConstantTimeCompare([]byte(fingerprint), []byte(installation.Fingerprint)) == 1
}

func copyInstallation(value identity.Installation) identity.Installation {
	copy := value
	copy.PublicKey = append(json.RawMessage(nil), value.PublicKey...)
	copy.Resources = append([]string(nil), value.Resources...)
	return copy
}

func (c *Client) resourceAllowed(resource string) bool {
	if !config.ValidID(resource) {
		return false
	}
	for _, allowed := range c.installation.Resources {
		if resource == allowed {
			return true
		}
	}
	return false
}

func randomNonce() (string, error) {
	var value [32]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", ErrClient
	}
	return hex.EncodeToString(value[:]), nil
}

func signAssertion(private ed25519.PrivateKey, id, audience, role, resource, epoch, nonce string, generation uint64, fingerprint string, now time.Time) (string, error) {
	claims, err := identity.NewAssertion(id, audience, role, resource, epoch, nonce, generation, now)
	if err != nil {
		return "", ErrClient
	}
	claims.Fingerprint = fingerprint
	proof, err := identity.Sign(private, claims)
	if err != nil {
		return "", ErrClient
	}
	return proof, nil
}

func (c *Client) post(ctx context.Context, path string, request, response any) error {
	data, err := json.Marshal(request)
	if err != nil || len(data) > maxBody {
		return ErrClient
	}
	exchange, cancel := context.WithTimeout(ctx, controlExchangeTimeout)
	defer cancel()
	httpRequest, err := http.NewRequestWithContext(exchange, http.MethodPost, c.gateway+path, bytes.NewReader(data))
	if err != nil {
		return ErrClient
	}
	httpRequest.GetBody = nil // control proofs are single-use and never retried.
	httpRequest.Header.Set("Content-Type", "application/json")
	httpResponse, err := c.client.Do(httpRequest)
	if err != nil {
		return ErrClient
	}
	defer httpResponse.Body.Close()
	if httpResponse.StatusCode != http.StatusOK || len(httpResponse.Header.Values("Content-Type")) != 1 || httpResponse.Header.Get("Content-Type") != "application/json" {
		return ErrClient
	}
	if decode(httpResponse.Body, response) != nil {
		return ErrClient
	}
	return nil
}

func (c *Client) challenge(ctx context.Context, resource, purpose, digest string) (string, error) {
	nonce, err := randomNonce()
	if err != nil {
		return "", ErrClient
	}
	proof, err := signAssertion(c.private, c.installation.ID, c.gateway+"/challenge", "connector", resource, c.installation.Epoch, nonce, c.installation.Generation, digest, time.Now())
	if err != nil {
		return "", ErrClient
	}
	var response ChallengeResponse
	if c.post(ctx, ChallengePath, ChallengeRequest{Installation: c.installation.ID, Resource: resource, Purpose: purpose, Digest: digest, Proof: proof}, &response) != nil || response.Schema != Schema || !validChallengeNonce(response.Nonce) {
		return "", ErrClient
	}
	return response.Nonce, nil
}

func validChallengeNonce(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 32 && hex.EncodeToString(decoded) == value
}

// Renew obtains a new bounded transport lease and, when csrDER is nonempty, a
// certificate for its public key. started is captured before challenge work so
// a caller can conservatively anchor the returned lease to request start.
func (c *Client) Renew(ctx context.Context, resource string, csrDER []byte) (response RenewalResponse, started time.Time, err error) {
	// Keep Go's monotonic reading: converting to UTC strips it and can let a
	// backward wall-clock adjustment extend a disconnected origin lease.
	started = time.Now()
	if c == nil || ctx == nil || ctx.Err() != nil || !c.resourceAllowed(resource) || len(csrDER) > 16384 {
		return RenewalResponse{}, started, ErrClient
	}
	if len(csrDER) > 0 && !validCSR(csrDER) {
		return RenewalResponse{}, started, ErrClient
	}
	digest := RenewalDigest(csrDER)
	nonce, challengeErr := c.challenge(ctx, resource, "renew", digest)
	if challengeErr != nil {
		return RenewalResponse{}, started, ErrClient
	}
	proof, proofErr := signAssertion(c.private, c.installation.ID, c.gateway+"/connector", "connector", resource, c.installation.Epoch, nonce, c.installation.Generation, "", time.Now())
	if proofErr != nil {
		return RenewalResponse{}, started, ErrClient
	}
	request := RenewalRequest{Installation: c.installation.ID, Resource: resource, Nonce: nonce, Proof: proof, CSR: encoded(csrDER)}
	if c.post(ctx, RenewalPath, request, &response) != nil || !validRenewal(response, c.installation, resource, csrDER) {
		return RenewalResponse{}, started, ErrClient
	}
	return response, started, nil
}

func validCSR(der []byte) bool {
	csr, err := x509.ParseCertificateRequest(der)
	if err != nil || csr.CheckSignature() != nil {
		return false
	}
	key, ok := csr.PublicKey.(ed25519.PublicKey)
	return ok && len(key) == ed25519.PublicKeySize
}

func validRenewal(response RenewalResponse, installation identity.Installation, resource string, csrDER []byte) bool {
	if response.Schema != Schema || response.Installation != installation.ID || response.Resource != resource || response.Epoch != installation.Epoch || response.Generation != installation.Generation || response.LeaseMilliseconds < 1 || response.LeaseMilliseconds > 45000 || !validTransportToken(response.TransportToken) {
		return false
	}
	certificate, err := decoded(response.Certificate, 16384)
	if err != nil {
		return false
	}
	if len(csrDER) == 0 {
		return len(certificate) == 0
	}
	csr, err := x509.ParseCertificateRequest(csrDER)
	if err != nil || csr.CheckSignature() != nil {
		return false
	}
	leaf, err := x509.ParseCertificate(certificate)
	name := installation.ID + ".connector.anvil-connect.internal"
	if err != nil || leaf.IsCA || leaf.Subject.CommonName != name || !relay.ExactPeerName(leaf, name) || leaf.KeyUsage != x509.KeyUsageDigitalSignature || len(leaf.ExtKeyUsage) != 1 || leaf.ExtKeyUsage[0] != x509.ExtKeyUsageServerAuth || len(leaf.UnknownExtKeyUsage) != 0 {
		return false
	}
	csrSPKI, err := x509.MarshalPKIXPublicKey(csr.PublicKey)
	if err != nil {
		return false
	}
	return bytes.Equal(csrSPKI, leaf.RawSubjectPublicKeyInfo)
}

func validTransportToken(raw string) bool {
	if len(raw) != 81 || !strings.HasPrefix(raw, "act1.") || raw[37] != '.' {
		return false
	}
	id, idErr := hex.DecodeString(raw[5:37])
	secret, secretErr := base64.RawURLEncoding.DecodeString(raw[38:])
	return idErr == nil && len(id) == 16 && hex.EncodeToString(id) == raw[5:37] && secretErr == nil && len(secret) == 32 && base64.RawURLEncoding.EncodeToString(secret) == raw[38:]
}

// Rotate proves possession of both keys and returns the next immutable
// installation snapshot. It never mutates this client's private key or copy of
// installation; callers create a new Client after persisting the new key.
func (c *Client) Rotate(ctx context.Context, resource string, newPublic json.RawMessage, newPrivate ed25519.PrivateKey, overlap time.Duration) (InstallationResponse, error) {
	if c == nil || ctx == nil || ctx.Err() != nil || !c.resourceAllowed(resource) || overlap < 0 || overlap > identity.MaximumRotationOverlap || overlap%time.Millisecond != 0 || len(newPrivate) != ed25519.PrivateKeySize {
		return InstallationResponse{}, ErrClient
	}
	canonical, fingerprint, err := identity.PublicKey(newPrivate)
	if err != nil || !bytes.Equal(canonical, newPublic) || fingerprint == c.installation.Fingerprint {
		return InstallationResponse{}, ErrClient
	}
	milliseconds := overlap.Milliseconds()
	digest := RotationDigest(canonical, milliseconds)
	nonce, err := c.challenge(ctx, resource, "rotate", digest)
	if err != nil {
		return InstallationResponse{}, ErrClient
	}
	now := time.Now()
	oldClaims, err := identity.NewRotationAssertion(c.installation.ID, c.gateway+"/rotate", "connector", c.installation.Epoch, nonce, fingerprint, c.installation.Generation, now)
	if err != nil {
		return InstallationResponse{}, ErrClient
	}
	oldProof, err := identity.Sign(c.private, oldClaims)
	if err != nil {
		return InstallationResponse{}, ErrClient
	}
	newClaims, err := identity.NewRotationAssertion(c.installation.ID, c.gateway+"/rotate", "connector", c.installation.Epoch, nonce, fingerprint, c.installation.Generation, now)
	if err != nil {
		return InstallationResponse{}, ErrClient
	}
	newProof, err := identity.Sign(newPrivate, newClaims)
	if err != nil {
		return InstallationResponse{}, ErrClient
	}
	var response InstallationResponse
	request := RotationRequest{Installation: c.installation.ID, Resource: resource, Nonce: nonce, PublicKey: encoded(canonical), OldProof: oldProof, NewProof: newProof, OverlapMilliseconds: milliseconds}
	if c.post(ctx, RotationPath, request, &response) != nil || response.Schema != Schema || response.Installation != c.installation.ID || response.Role != "connector" || response.Status != "active" || response.Epoch != c.installation.Epoch || response.Generation != c.installation.Generation+1 || response.Fingerprint != fingerprint {
		return InstallationResponse{}, ErrClient
	}
	return response, nil
}

// Close releases only idle sockets owned by this client. It does not revoke a
// transport lease and does not mutate installation authority.
func (c *Client) Close() {
	if c != nil && c.transport != nil {
		c.transport.CloseIdleConnections()
	}
}
