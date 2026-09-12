package runtime

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/control"
	"github.com/fakoli/anvil-serving/connect/internal/credential"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/origin"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
	"golang.org/x/sys/unix"
)

const connectorStateSchema = "anvil-connect.connector-state/v1"

type connectorResourceState struct {
	ID         string `json:"id"`
	CSR        string `json:"csr"`
	TLSPrivate string `json:"tls_private"`
}

type connectorState struct {
	Schema       string                   `json:"schema"`
	Status       string                   `json:"status"`
	Invitation   string                   `json:"invitation"`
	ControlHost  string                   `json:"control_host"`
	TunnelHost   string                   `json:"tunnel_host"`
	InnerCAPEM   string                   `json:"inner_ca_pem"`
	ID           string                   `json:"id"`
	Role         string                   `json:"role"`
	Resources    []string                 `json:"resources"`
	PublicKey    string                   `json:"public_key"`
	PrivateKey   string                   `json:"private_key"`
	Fingerprint  string                   `json:"fingerprint"`
	Generation   uint64                   `json:"generation"`
	Epoch        string                   `json:"epoch"`
	ResourceKeys []connectorResourceState `json:"resource_keys"`
}

// InitializeConnector creates exactly one installation identity before it
// attempts enrollment. On a transport failure the pending file remains, so an
// operator can retry with the same key and invitation rather than silently
// creating a different identity. A successful but lost response remains
// recoverable as pending local state; invitation replay is intentionally not
// attempted automatically.
func InitializeConnector(ctx context.Context, declaration ConnectorConfig, bundle admin.Response) error {
	if ctx == nil || ctx.Err() != nil || declaration.Validate() != nil || !validInviteBundle(declaration, bundle) {
		return ErrConfiguration
	}
	roots, err := readPublicTrustFile(declaration.PublicTrustFile)
	if err != nil || !validPublicCA(bundle.InnerCAPEM) {
		return ErrConfiguration
	}
	directory, err := privatefiles.Open(declaration.StateDirectory)
	if err != nil {
		return ErrUnavailable
	}
	defer directory.Close()
	state, exists, err := loadConnectorState(directory)
	if err != nil {
		return ErrUnavailable
	}
	if !exists {
		state, err = newConnectorState(declaration, bundle)
		if err != nil {
			return ErrUnavailable
		}
		data, err := json.Marshal(state)
		if err != nil || directory.Create("installation.json", data) != nil {
			return ErrUnavailable
		}
	}
	if !sameConnectorBinding(state, declaration, bundle) {
		return ErrConfiguration
	}
	if state.Status == "pending" && state.Invitation != bundle.Invitation {
		state.Invitation = bundle.Invitation
		data, encodeErr := json.Marshal(state)
		if encodeErr != nil || directory.Replace("installation.json", data) != nil {
			return ErrUnavailable
		}
	}
	_, private, err := state.installation()
	if err != nil {
		return ErrUnavailable
	}
	if state.Status == "enrolled" {
		return nil
	}
	metadata := identity.Invitation{Installation: state.ID, Role: state.Role, Resources: append([]string(nil), state.Resources...), Epoch: state.Epoch, Generation: state.Generation}
	enrolled, err := control.Enroll(ctx, "https://"+declaration.ControlHost, state.Invitation, metadata, private, control.ClientOptions{RootCAs: roots, HTTPProxyURL: declaration.HTTPProxyURL})
	if err != nil {
		return ErrUnavailable
	}
	state.Status = "enrolled"
	state.Invitation = "" // raw invitation is no longer needed after response verification.
	state.PublicKey = base64.RawURLEncoding.EncodeToString(enrolled.PublicKey)
	data, err := json.Marshal(state)
	if err != nil || directory.Replace("installation.json", data) != nil {
		return ErrUnavailable
	}
	return nil
}

// ConnectorIdentity returns only the locally persisted public installation
// snapshot after checking it still matches the immutable connector declaration.
func ConnectorIdentity(declaration ConnectorConfig) (identity.Installation, error) {
	if declaration.Validate() != nil {
		return identity.Installation{}, ErrConfiguration
	}
	directory, err := privatefiles.Open(declaration.StateDirectory)
	if err != nil {
		return identity.Installation{}, ErrUnavailable
	}
	defer directory.Close()
	state, exists, err := loadConnectorState(directory)
	if err != nil || !exists || state.ID != declaration.ID || state.ControlHost != declaration.ControlHost || state.TunnelHost != declaration.TunnelHost || !sameResources(state.Resources, resourceIDs(declaration)) {
		return identity.Installation{}, ErrUnavailable
	}
	installation, _, err := state.installation()
	if err != nil {
		return identity.Installation{}, ErrUnavailable
	}
	installation.PreviousPublicKey = nil
	installation.PreviousFingerprint = ""
	installation.PreviousGeneration = 0
	installation.PreviousUntil = time.Time{}
	return installation, nil
}

func validInviteBundle(c ConnectorConfig, b admin.Response) bool {
	if b.Operation != "invite" || !validRawInvitation(b.Invitation) || b.Installation != c.ID || b.Role != "connector" || b.ControlHost != c.ControlHost || b.TunnelHost != c.TunnelHost || !validEpoch(b.Epoch) || b.Generation == 0 || !validPublicCA(b.InnerCAPEM) {
		return false
	}
	return sameResources(resourceIDs(c), b.Resources)
}

func validRawInvitation(value string) bool {
	if len(value) != 81 || !strings.HasPrefix(value, "aci1.") || value[37] != '.' {
		return false
	}
	id, err := hex.DecodeString(value[5:37])
	if err != nil || len(id) != 16 || hex.EncodeToString(id) != value[5:37] {
		return false
	}
	secret, err := base64.RawURLEncoding.DecodeString(value[38:])
	return err == nil && len(secret) == 32 && base64.RawURLEncoding.EncodeToString(secret) == value[38:]
}
func validEpoch(value string) bool {
	raw, err := hex.DecodeString(value)
	return err == nil && len(raw) == 32 && hex.EncodeToString(raw) == value
}

func resourceIDs(c ConnectorConfig) []string {
	ids := make([]string, 0, len(c.Resources))
	for _, resource := range c.Resources {
		ids = append(ids, resource.Envelope.Rule.ID)
	}
	sort.Strings(ids)
	return ids
}

func sameResources(left, right []string) bool {
	left, right = append([]string(nil), left...), append([]string(nil), right...)
	sort.Strings(left)
	sort.Strings(right)
	return len(left) == len(right) && bytes.Equal([]byte(strings.Join(left, "\x00")), []byte(strings.Join(right, "\x00")))
}

func canonicalResources(resources []string) bool {
	if len(resources) < 1 || len(resources) > 64 {
		return false
	}
	copy := append([]string(nil), resources...)
	sort.Strings(copy)
	for index, resource := range copy {
		if !config.ValidID(resource) || (index > 0 && resource == copy[index-1]) {
			return false
		}
	}
	return true
}

func newConnectorState(c ConnectorConfig, b admin.Response) (connectorState, error) {
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return connectorState{}, err
	}
	public, fingerprint, err := identity.PublicKey(private)
	if err != nil {
		return connectorState{}, err
	}
	state := connectorState{Schema: connectorStateSchema, Status: "pending", Invitation: b.Invitation, ControlHost: c.ControlHost, TunnelHost: c.TunnelHost, InnerCAPEM: b.InnerCAPEM, ID: c.ID, Role: "connector", Resources: resourceIDs(c), PublicKey: base64.RawURLEncoding.EncodeToString(public), PrivateKey: base64.RawURLEncoding.EncodeToString(private), Fingerprint: fingerprint, Generation: b.Generation, Epoch: b.Epoch, ResourceKeys: make([]connectorResourceState, 0, len(c.Resources))}
	for _, id := range state.Resources {
		tlsPrivate, csr, err := credential.GenerateCSR()
		if err != nil {
			return connectorState{}, err
		}
		if bytes.Equal(tlsPrivate.Public().(ed25519.PublicKey), private.Public().(ed25519.PublicKey)) {
			return connectorState{}, errors.New("key domains overlap")
		}
		state.ResourceKeys = append(state.ResourceKeys, connectorResourceState{ID: id, CSR: base64.RawURLEncoding.EncodeToString(csr), TLSPrivate: base64.RawURLEncoding.EncodeToString(tlsPrivate)})
	}
	return state, nil
}

func loadConnectorState(directory *privatefiles.Directory) (connectorState, bool, error) {
	data, err := directory.Read("installation.json", 256*1024)
	if errors.Is(err, privatefiles.ErrPrivate) {
		return connectorState{}, false, nil
	}
	var state connectorState
	if err != nil || config.Decode(bytes.NewReader(data), &state) != nil || !validConnectorState(state) {
		return connectorState{}, false, ErrConfiguration
	}
	return state, true, nil
}

func validConnectorState(s connectorState) bool {
	if s.Schema != connectorStateSchema || (s.Status != "pending" && s.Status != "enrolled") || !config.ValidHost(s.ControlHost) || !config.ValidHost(s.TunnelHost) || s.ControlHost == s.TunnelHost || !config.ValidID(s.ID) || s.Role != "connector" || s.Generation == 0 || !validEpoch(s.Epoch) || !validPublicCA(s.InnerCAPEM) || !canonicalResources(s.Resources) || len(s.ResourceKeys) != len(s.Resources) {
		return false
	}
	_, private, err := s.installation()
	if err != nil {
		return false
	}
	joseSPKI, publicErr := x509.MarshalPKIXPublicKey(private.Public())
	if publicErr != nil {
		return false
	}
	seen := map[string]bool{}
	seenKeys := map[string]bool{}
	for _, entry := range s.ResourceKeys {
		if !config.ValidID(entry.ID) || seen[entry.ID] || !contains(s.Resources, entry.ID) {
			return false
		}
		seen[entry.ID] = true
		csr, e1 := base64.RawURLEncoding.DecodeString(entry.CSR)
		key, e2 := base64.RawURLEncoding.DecodeString(entry.TLSPrivate)
		if e1 != nil || e2 != nil || len(key) != ed25519.PrivateKeySize {
			return false
		}
		request, e3 := x509.ParseCertificateRequest(csr)
		if e3 != nil || request.CheckSignature() != nil {
			return false
		}
		csrPublic, e4 := x509.MarshalPKIXPublicKey(request.PublicKey)
		tlsPublic, e5 := x509.MarshalPKIXPublicKey(ed25519.PrivateKey(key).Public())
		if e4 != nil || e5 != nil || !bytes.Equal(csrPublic, tlsPublic) || bytes.Equal(tlsPublic, joseSPKI) || seenKeys[base64.RawURLEncoding.EncodeToString(tlsPublic)] {
			return false
		}
		seenKeys[base64.RawURLEncoding.EncodeToString(tlsPublic)] = true
	}
	return true
}

func contains(items []string, wanted string) bool {
	for _, item := range items {
		if item == wanted {
			return true
		}
	}
	return false
}

func (s connectorState) installation() (identity.Installation, ed25519.PrivateKey, error) {
	public, err := base64.RawURLEncoding.DecodeString(s.PublicKey)
	if err != nil {
		return identity.Installation{}, nil, err
	}
	private, err := base64.RawURLEncoding.DecodeString(s.PrivateKey)
	if err != nil || len(private) != ed25519.PrivateKeySize {
		return identity.Installation{}, nil, ErrConfiguration
	}
	canonical, fingerprint, err := identity.PublicKey(ed25519.PrivateKey(private))
	if err != nil || fingerprint != s.Fingerprint || !bytes.Equal(canonical, public) {
		return identity.Installation{}, nil, ErrConfiguration
	}
	return identity.Installation{ID: s.ID, Role: s.Role, Resources: append([]string(nil), s.Resources...), PublicKey: json.RawMessage(public), Fingerprint: s.Fingerprint, Generation: s.Generation, Epoch: s.Epoch, Status: s.Status}, ed25519.PrivateKey(private), nil
}

func sameConnectorBinding(s connectorState, c ConnectorConfig, b admin.Response) bool {
	return s.ID == c.ID && s.ControlHost == c.ControlHost && s.TunnelHost == c.TunnelHost && s.Role == "connector" && s.Generation == b.Generation && s.Epoch == b.Epoch && s.InnerCAPEM == b.InnerCAPEM && sameResources(s.Resources, resourceIDs(c))
}

func readManagedMaterial(path string, private bool) ([]byte, error) {
	f, err := os.OpenFile(path, os.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, ErrConfiguration
	}
	defer f.Close()
	info, err := f.Stat()
	var stat unix.Stat_t
	if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 1024*1024 || info.Mode().Perm()&0022 != 0 || unix.Fstat(int(f.Fd()), &stat) != nil || (stat.Uid != 0 && stat.Uid != uint32(os.Geteuid())) {
		return nil, ErrConfiguration
	}
	if private && (info.Mode().Perm() != 0600 || stat.Uid != uint32(os.Geteuid()) || stat.Nlink != 1) {
		return nil, ErrConfiguration
	}
	data, readErr := io.ReadAll(io.LimitReader(f, 1024*1024+1))
	if readErr != nil || len(data) < 1 || len(data) > 1024*1024 {
		return nil, ErrConfiguration
	}
	return data, nil
}

func readPublicTrustFile(path string) (*x509.CertPool, error) {
	data, err := readManagedMaterial(path, false)
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	count := 0
	for len(bytes.TrimSpace(data)) != 0 {
		block, rest := pem.Decode(data)
		if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 {
			return nil, ErrConfiguration
		}
		certificate, err := x509.ParseCertificate(block.Bytes)
		if err != nil || !certificate.IsCA || !certificate.BasicConstraintsValid {
			return nil, ErrConfiguration
		}
		roots.AddCert(certificate)
		count++
		data = rest
	}
	if count == 0 {
		return nil, ErrConfiguration
	}
	return roots, nil
}

func validPublicCA(raw string) bool {
	roots, err := parsePublicCA(raw)
	return err == nil && roots != nil
}
func parsePublicCA(raw string) (*x509.CertPool, error) {
	if len(raw) < 1 || len(raw) > 64*1024 {
		return nil, ErrConfiguration
	}
	data := []byte(raw)
	block, rest := pem.Decode(data)
	if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 || len(bytes.TrimSpace(rest)) != 0 {
		return nil, ErrConfiguration
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil || !certificate.IsCA || !certificate.BasicConstraintsValid {
		return nil, ErrConfiguration
	}
	roots := x509.NewCertPool()
	roots.AddCert(certificate)
	return roots, nil
}

type Connector struct {
	events  relay.Events
	cancel  context.CancelFunc
	close   sync.Once
	cleanup []func()
	done    chan struct{}
	errors  chan error
	wait    sync.WaitGroup
}

func (c *Connector) Done() <-chan struct{} { return c.done }
func (c *Connector) Events() []relay.Event { return c.events.Snapshot() }
func (c *Connector) Close() {
	c.close.Do(func() {
		c.cancel()
		for i := len(c.cleanup) - 1; i >= 0; i-- {
			c.cleanup[i]()
		}
		c.wait.Wait()
		close(c.done)
	})
}
func (c *Connector) Wait(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return nil
	case <-c.done:
		return nil
	case <-c.errors:
		return ErrUnavailable
	}
}

// StartConnector starts only an enrolled, immutable local connector state.
// Each origin remains closed until its initial authenticated renewal succeeds.
// A returned handle means owned processes started, not reverse-registration
// readiness. The pin supplies no readiness IPC; activation must prove admission
// independently before treating this handle as a healthy managed deployment.
func StartConnector(parent context.Context, declaration ConnectorConfig, secrets SecretSource) (_ *Connector, result error) {
	if parent == nil || parent.Err() != nil || declaration.Validate() != nil || secrets == nil {
		return nil, ErrConfiguration
	}
	ctx, cancel := context.WithCancel(parent)
	runtime := &Connector{cancel: cancel, done: make(chan struct{}), errors: make(chan error, len(declaration.Resources)+1)}
	defer func() {
		if result != nil {
			runtime.Close()
		}
	}()
	directory, err := privatefiles.Open(declaration.StateDirectory)
	if err != nil {
		return nil, ErrUnavailable
	}
	runtime.cleanup = append(runtime.cleanup, func() { _ = directory.Close() })
	state, exists, err := loadConnectorState(directory)
	if err != nil || !exists || (state.Status != "enrolled" && state.Status != "pending") || state.ID != declaration.ID || state.ControlHost != declaration.ControlHost || state.TunnelHost != declaration.TunnelHost || !sameResources(state.Resources, resourceIDs(declaration)) {
		return nil, ErrUnavailable
	}
	installation, private, err := state.installation()
	if err != nil {
		return nil, ErrUnavailable
	}
	publicRoots, err := readPublicTrustFile(declaration.PublicTrustFile)
	if err != nil {
		return nil, ErrUnavailable
	}
	innerRoots, err := parsePublicCA(state.InnerCAPEM)
	if err != nil {
		return nil, ErrUnavailable
	}
	outer := declaration.outerClient()
	path := "public"
	if outer.Local != nil {
		path = "local"
		root, roots, err := localRoot(outer.TrustFile, publicRoots, innerRoots)
		publicPEM, readErr := readManagedMaterial(declaration.PublicTrustFile, false)
		if err != nil || readErr != nil || !distinctRoot(root, publicPEM) || !distinctRoot(root, []byte(state.InnerCAPEM)) || transport.VerifyLocalEntry(ctx, *outer.Local, roots) != nil {
			runtime.events.Record(path, "", "entry_tls_failed")
			return nil, ErrUnavailable
		}
	}
	headers, err := directory.Subdirectory("headers")
	if err != nil {
		return nil, ErrUnavailable
	}
	runtime.cleanup = append(runtime.cleanup, func() { _ = headers.Close() })
	headerPath, err := directory.PinPath("headers")
	if err != nil {
		return nil, ErrUnavailable
	}
	runtime.cleanup = append(runtime.cleanup, func() { _ = headerPath.Close() })
	empty, err := directory.Subdirectory("empty-roots")
	if err != nil {
		return nil, ErrUnavailable
	}
	runtime.cleanup = append(runtime.cleanup, func() { _ = empty.Close() })
	emptyPath, err := directory.PinPath("empty-roots")
	if err != nil {
		return nil, ErrUnavailable
	}
	runtime.cleanup = append(runtime.cleanup, func() { _ = emptyPath.Close() })
	for _, resource := range declaration.Resources {
		client, err := control.NewClient("https://"+declaration.ControlHost, installation, private, control.ClientOptions{RootCAs: publicRoots, HTTPProxyURL: declaration.HTTPProxyURL})
		if err != nil {
			return nil, ErrUnavailable
		}
		runtime.cleanup = append(runtime.cleanup, client.Close)
		key, ok := resourceState(state, resource.Envelope.Rule.ID)
		if !ok {
			return nil, ErrUnavailable
		}
		csr, err := base64.RawURLEncoding.DecodeString(key.CSR)
		if err != nil {
			return nil, ErrUnavailable
		}
		response, started, err := client.Renew(ctx, resource.Envelope.Rule.ID, csr)
		if err != nil {
			runtime.events.Record(path, resource.Envelope.Rule.ID, "renewal_failed")
			return nil, ErrUnavailable
		}
		tlsPrivate, err := decodeTLSPrivate(key.TLSPrivate)
		if err != nil {
			return nil, ErrUnavailable
		}
		leaf, certificate, err := verifyRenewalCertificate(response.Certificate, csr, tlsPrivate, innerRoots, installation.ID)
		if err != nil {
			return nil, ErrUnavailable
		}
		lease, err := access.NewLease(access.LeaseBinding{Installation: installation.ID, Resource: resource.Envelope.Rule.ID, Epoch: installation.Epoch, Generation: installation.Generation}, nil)
		if err != nil {
			return nil, ErrUnavailable
		}
		if err := lease.RenewFor(lease.Binding(), 1, started, time.Duration(response.LeaseMilliseconds)*time.Millisecond); err != nil {
			return nil, ErrUnavailable
		}
		if headers.Replace(resource.Envelope.Rule.ID+".headers", []byte("Authorization: Bearer "+response.TransportToken+"\n")) != nil {
			return nil, ErrUnavailable
		}
		if state.Status == "pending" {
			state.Status, state.Invitation = "enrolled", ""
			data, encodeErr := json.Marshal(state)
			if encodeErr != nil || directory.Replace("installation.json", data) != nil {
				return nil, ErrUnavailable
			}
		}
		proxy, err := newOrigin(resource.Envelope, lease, secrets)
		if err != nil {
			return nil, ErrUnavailable
		}
		runtime.cleanup = append(runtime.cleanup, proxy.Close)
		var current atomic.Pointer[tls.Certificate]
		current.Store(certificate)
		listener, err := net.Listen("tcp4", resource.Envelope.Listen)
		if err != nil {
			return nil, ErrUnavailable
		}
		server := nativeServer(ctx, proxy)
		protocols := new(http.Protocols)
		protocols.SetHTTP1(true)
		protocols.SetHTTP2(true)
		server.Protocols = protocols
		server.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS13, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: innerRoots.Clone(), GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
			value := current.Load()
			if value == nil {
				return nil, ErrUnavailable
			}
			return value, nil
		}}
		runtime.cleanup = append(runtime.cleanup, func() { _ = server.Close(); _ = listener.Close() })
		runtime.wait.Add(1)
		go func() {
			defer runtime.wait.Done()
			if err := server.ServeTLS(listener, "", ""); err != nil && !errors.Is(err, http.ErrServerClosed) {
				select {
				case runtime.errors <- ErrUnavailable:
				default:
				}
				cancel()
			}
		}()
		options := outer
		options.ReverseAddress, options.OriginAddress = resource.ReverseAddress, resource.Envelope.Listen
		options.EmptyTrustDirectory, options.HeadersFile = emptyPath.Path(), headerPath.Path()+"/"+resource.Envelope.Rule.ID+".headers"
		process, err := transport.StartClient(ctx, options)
		if err != nil {
			runtime.events.Record(path, resource.Envelope.Rule.ID, "tunnel_establishment_failed")
			return nil, ErrUnavailable
		}
		runtime.cleanup = append(runtime.cleanup, func() { _ = process.Close() })
		runtime.wait.Add(3)
		go func() {
			defer runtime.wait.Done()
			watchLease(ctx, lease, &runtime.events, path, resource.Envelope.Rule.ID)
		}()
		go func(p *transport.Process) {
			defer runtime.wait.Done()
			select {
			case <-ctx.Done():
			case <-p.Done():
				// A PID proves neither establishment nor a previous registration.
				if p.Failed() {
					runtime.events.Record(path, resource.Envelope.Rule.ID, "tunnel_establishment_failed")
				} else {
					// The child ran and then ended without a process-level
					// failure: record the lifecycle end rather than implying
					// establishment never happened. Activation readiness
					// (Slice 3) is what turns this split into proof.
					runtime.events.Record(path, resource.Envelope.Rule.ID, "tunnel_disconnected")
				}
				select {
				case runtime.errors <- ErrUnavailable:
				default:
				}
				cancel()
			}
		}(process)
		go connectorRenewLoop(ctx, &runtime.wait, client, resource.Envelope.Rule.ID, csr, tlsPrivate, innerRoots, installation.ID, lease, &current, headers, &runtime.events, path)
		_ = leaf
	}
	return runtime, nil
}

func watchLease(ctx context.Context, lease *access.Lease, events *relay.Events, path, resource string) {
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	expired := false
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		denied := lease.Check() != nil
		if denied && !expired {
			events.Record(path, resource, "lease_expired")
		}
		expired = denied
	}
}

func resourceState(state connectorState, id string) (connectorResourceState, bool) {
	for _, entry := range state.ResourceKeys {
		if entry.ID == id {
			return entry, true
		}
	}
	return connectorResourceState{}, false
}
func newOrigin(envelope config.Envelope, lease *access.Lease, secrets SecretSource) (*origin.Proxy, error) {
	if envelope.Rule.Access == "browser" {
		return origin.NewBrowser(envelope, transport.GatewayPeer, lease)
	}
	return origin.NewAPI(envelope, transport.GatewayPeer, origin.SecretSource(secrets), lease)
}
func verifyRenewalCertificate(encodedCSR string, csrDER []byte, key ed25519.PrivateKey, roots *x509.CertPool, id string) (*x509.Certificate, *tls.Certificate, error) {
	der, err := base64.RawURLEncoding.DecodeString(encodedCSR)
	if err != nil {
		return nil, nil, err
	}
	leaf, err := x509.ParseCertificate(der)
	if err != nil {
		return nil, nil, err
	}
	csr, err := x509.ParseCertificateRequest(csrDER)
	if err != nil || csr.CheckSignature() != nil {
		return nil, nil, ErrUnavailable
	}
	a, err := x509.MarshalPKIXPublicKey(csr.PublicKey)
	b, e := x509.MarshalPKIXPublicKey(leaf.PublicKey)
	owned, ownErr := x509.MarshalPKIXPublicKey(key.Public())
	if err != nil || e != nil || ownErr != nil || !bytes.Equal(a, b) || !bytes.Equal(a, owned) || !relay.ExactPeerName(leaf, transport.ConnectorPeer(id)) {
		return nil, nil, ErrUnavailable
	}
	if _, err = leaf.Verify(x509.VerifyOptions{Roots: roots, DNSName: transport.ConnectorPeer(id), KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}); err != nil {
		return nil, nil, err
	}
	return leaf, &tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}, nil
}
func decodeTLSPrivate(raw string) (ed25519.PrivateKey, error) {
	value, err := base64.RawURLEncoding.DecodeString(raw)
	if err != nil || len(value) != ed25519.PrivateKeySize {
		return nil, ErrUnavailable
	}
	return ed25519.PrivateKey(value), nil
}
func connectorRenewLoop(ctx context.Context, w *sync.WaitGroup, client *control.Client, resource string, csr []byte, key ed25519.PrivateKey, roots *x509.CertPool, id string, lease *access.Lease, current *atomic.Pointer[tls.Certificate], headers *privatefiles.Directory, events *relay.Events, path string) {
	defer w.Done()
	sequence := uint64(1)
	timer := time.NewTimer(20 * time.Second)
	defer timer.Stop()
	failed := false
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}
		sequence++ // every attempt consumes a local sequence, even a failed write.
		response, started, err := client.Renew(ctx, resource, csr)
		if err == nil {
			_, certificate, e := verifyRenewalCertificate(response.Certificate, csr, key, roots, id)
			if e == nil && headers.Replace(resource+".headers", []byte("Authorization: Bearer "+response.TransportToken+"\n")) == nil && lease.RenewFor(lease.Binding(), sequence, started, time.Duration(response.LeaseMilliseconds)*time.Millisecond) == nil {
				current.Store(certificate)
				if failed {
					events.Record(path, resource, "renewal_resumed")
				}
				failed = false
				timer.Reset(20 * time.Second)
				continue
			}
		}
		events.Record(path, resource, "renewal_failed")
		failed = true
		timer.Reset(5 * time.Second)
	}
}
