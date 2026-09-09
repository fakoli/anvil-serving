// Package credential binds a connector's TLS server key to its separately
// enrolled JOSE installation identity. It keeps CA signing material in memory
// and stores only public, bounded SPKI bindings in Connect authority state.
package credential

import (
	"bytes"
	"crypto"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/json"
	"errors"
	"math/big"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	jose "github.com/go-jose/go-jose/v4"
)

var (
	ErrConfiguration = errors.New("invalid connector certificate authority")
	ErrDenied        = errors.New("connector certificate denied")
)

const certificateLifetime = time.Hour

var leafBindingOID = asn1.ObjectIdentifier{1, 3, 6, 1, 4, 1, 55555, 1, 1}

// Certificate is public certificate material returned to a connector. The
// matching TLS private key is generated and retained locally by that connector.
type Certificate struct {
	DER      []byte
	NotAfter time.Time
}

type installationSnapshot struct {
	ID          string          `json:"id"`
	Role        string          `json:"role"`
	PublicKey   json.RawMessage `json:"public_key"`
	Fingerprint string          `json:"fingerprint"`
	Generation  uint64          `json:"generation"`
	Epoch       string          `json:"epoch"`
}

type binding struct {
	Installation installationSnapshot `json:"installation"`
	SPKI         [32]byte             `json:"spki"`
	NotAfter     time.Time            `json:"not_after"`
}

// leafBinding is issuer-chosen, signed certificate metadata. It prevents an
// old leaf with a reused TLS key from acquiring a newer JOSE generation simply
// because its SPKI is still current.
type leafBinding struct {
	Version     uint8  `json:"v"`
	ID          string `json:"id"`
	Role        string `json:"role"`
	Fingerprint string `json:"fingerprint"`
	Generation  uint64 `json:"generation"`
	Epoch       string `json:"epoch"`
}

// bindingRecord is the only persisted TLS state per declared resource. Its
// prior slot exists solely for the bounded immediate JOSE rotation overlap.
type bindingRecord struct {
	Resource string   `json:"resource"`
	Current  binding  `json:"current"`
	Previous *binding `json:"previous,omitempty"`
}

type Issuer struct {
	identity  *identity.Manager
	state     *store.Store
	resources map[string]config.Resource
	ca        *x509.Certificate
	signer    crypto.Signer
	roots     *x509.CertPool
}

// New accepts only an in-memory signing key matching the supplied direct CA.
// The State must be the same authority state supplied to identity.Manager.
func New(manager *identity.Manager, state *store.Store, gateway config.Gateway, ca *x509.Certificate, signer crypto.Signer) (*Issuer, error) {
	if manager == nil || state == nil || gateway.Validate() != nil || ca == nil || len(ca.Raw) == 0 || signer == nil {
		return nil, ErrConfiguration
	}
	// Reparse an owned DER copy. A caller can mutate fields on a parsed
	// Certificate after construction, so Raw/CA fields must not be trusted as
	// a durable authority boundary.
	ownedCA, err := x509.ParseCertificate(append([]byte(nil), ca.Raw...))
	if err != nil || !ownedCA.IsCA || !ownedCA.BasicConstraintsValid || ownedCA.KeyUsage&x509.KeyUsageCertSign == 0 {
		return nil, ErrConfiguration
	}
	caPublic, err := x509.MarshalPKIXPublicKey(ownedCA.PublicKey)
	if err != nil {
		return nil, ErrConfiguration
	}
	signerPublic, err := x509.MarshalPKIXPublicKey(signer.Public())
	if err != nil || !bytes.Equal(caPublic, signerPublic) {
		return nil, ErrConfiguration
	}
	issuer := &Issuer{identity: manager, state: state, resources: map[string]config.Resource{}, ca: ownedCA, signer: signer, roots: x509.NewCertPool()}
	issuer.roots.AddCert(ownedCA)
	for _, resource := range gateway.Resources {
		// Resource values are read for every peer check, so retain no caller
		// owned slice backing arrays.
		copied := resource
		copied.Rule.Methods = append([]string(nil), resource.Rule.Methods...)
		issuer.resources[copied.Rule.ID] = copied
	}
	return issuer, nil
}

// GenerateCSR creates a distinct local Ed25519 TLS key and an identity-free
// CSR. Its private key is returned only to the local caller and is never
// accepted, persisted, or returned by Issuer.
func GenerateCSR() (ed25519.PrivateKey, []byte, error) {
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, nil, ErrDenied
	}
	csr, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{}, private)
	if err != nil {
		return nil, nil, ErrDenied
	}
	return private, csr, nil
}

func recordKey(resource string) string { return "tls:" + resource }

func connectorName(id string) string { return id + ".connector.anvil-connect.internal" }

func fromInstallation(snapshot identity.Installation) installationSnapshot {
	return installationSnapshot{ID: snapshot.ID, Role: snapshot.Role, PublicKey: append(json.RawMessage(nil), snapshot.PublicKey...), Fingerprint: snapshot.Fingerprint, Generation: snapshot.Generation, Epoch: snapshot.Epoch}
}

func (snapshot installationSnapshot) installation() identity.Installation {
	return identity.Installation{ID: snapshot.ID, Role: snapshot.Role, PublicKey: append(json.RawMessage(nil), snapshot.PublicKey...), Fingerprint: snapshot.Fingerprint, Generation: snapshot.Generation, Epoch: snapshot.Epoch}
}

func sameSnapshot(left, right installationSnapshot) bool {
	return left.ID == right.ID && left.Role == right.Role && left.Fingerprint == right.Fingerprint && left.Generation == right.Generation && left.Epoch == right.Epoch && bytes.Equal(left.PublicKey, right.PublicKey)
}

func sameBinding(left, right binding) bool {
	return sameSnapshot(left.Installation, right.Installation) && left.SPKI == right.SPKI && left.NotAfter.Equal(right.NotAfter)
}

func sameRecord(left, right bindingRecord) bool {
	if left.Resource != right.Resource || !sameBinding(left.Current, right.Current) || (left.Previous == nil) != (right.Previous == nil) {
		return false
	}
	return left.Previous == nil || sameBinding(*left.Previous, *right.Previous)
}

func certificateBinding(snapshot installationSnapshot) ([]byte, error) {
	return json.Marshal(leafBinding{
		Version:     1,
		ID:          snapshot.ID,
		Role:        snapshot.Role,
		Fingerprint: snapshot.Fingerprint,
		Generation:  snapshot.Generation,
		Epoch:       snapshot.Epoch,
	})
}

func leafBindingMatches(leaf *x509.Certificate, snapshot installationSnapshot) bool {
	expected, err := certificateBinding(snapshot)
	if err != nil {
		return false
	}
	count := 0
	for _, extension := range leaf.Extensions {
		if extension.Id.Equal(leafBindingOID) {
			count++
			if !bytes.Equal(extension.Value, expected) {
				return false
			}
		}
	}
	return count == 1
}

func copyRecord(record bindingRecord) bindingRecord {
	result := record
	result.Current.Installation.PublicKey = append(json.RawMessage(nil), record.Current.Installation.PublicKey...)
	if record.Previous != nil {
		previous := *record.Previous
		previous.Installation.PublicKey = append(json.RawMessage(nil), record.Previous.Installation.PublicKey...)
		result.Previous = &previous
	}
	return result
}

func (i *Issuer) resource(resource string, snapshot identity.Installation) (config.Resource, bool) {
	declaration, exists := i.resources[resource]
	return declaration, exists && declaration.Connector == snapshot.ID && snapshot.Role == "connector"
}

func (i *Issuer) now() (time.Time, error) {
	var now time.Time
	err := i.state.View(func(tx *store.Tx) error {
		now = tx.Now()
		return nil
	})
	if err != nil {
		return time.Time{}, ErrDenied
	}
	return now, nil
}

func csrKey(csrDER []byte) (ed25519.PublicKey, [32]byte, error) {
	csr, err := x509.ParseCertificateRequest(csrDER)
	if err != nil || csr.CheckSignature() != nil {
		return nil, [32]byte{}, ErrDenied
	}
	key, ok := csr.PublicKey.(ed25519.PublicKey)
	if !ok || len(key) != ed25519.PublicKeySize {
		return nil, [32]byte{}, ErrDenied
	}
	spki, err := x509.MarshalPKIXPublicKey(key)
	if err != nil {
		return nil, [32]byte{}, ErrDenied
	}
	return key, sha256.Sum256(spki), nil
}

func distinctJOSEKey(snapshot identity.Installation, key ed25519.PublicKey) bool {
	var jwk jose.JSONWebKey
	if json.Unmarshal(snapshot.PublicKey, &jwk) != nil || !jwk.Valid() || !jwk.IsPublic() {
		return false
	}
	installed, ok := jwk.Key.(ed25519.PublicKey)
	return ok && len(installed) == ed25519.PublicKeySize && !bytes.Equal(installed, key)
}

func serialNumber() (*big.Int, error) {
	limit := new(big.Int).Lsh(big.NewInt(1), 128)
	serial, err := rand.Int(rand.Reader, limit)
	if err != nil || serial.Sign() == 0 {
		return nil, ErrDenied
	}
	return serial, nil
}

// Issue requires a snapshot obtained from a fresh identity.Verify call. The
// caller must first bind its server challenge to the exact SHA-256 CSR DER,
// declared resource, and certificate purpose; this method does not accept a
// caller-decoded assertion or infer that challenge binding itself.
func (i *Issuer) Issue(snapshot identity.Installation, resource string, csrDER []byte) (Certificate, error) {
	if _, ok := i.resource(resource, snapshot); !ok || len(csrDER) == 0 || len(csrDER) > 16384 || i.identity.Check(snapshot, resource) != nil {
		return Certificate{}, ErrDenied
	}
	key, spki, err := csrKey(csrDER)
	if err != nil || !distinctJOSEKey(snapshot, key) {
		return Certificate{}, ErrDenied
	}
	now, err := i.now()
	if err != nil || now.Before(i.ca.NotBefore) || !now.Before(i.ca.NotAfter) {
		return Certificate{}, ErrDenied
	}
	notAfter := now.Add(certificateLifetime)
	if i.ca.NotAfter.Before(notAfter) {
		notAfter = i.ca.NotAfter
	}
	if !now.Before(notAfter) {
		return Certificate{}, ErrDenied
	}
	serial, err := serialNumber()
	if err != nil {
		return Certificate{}, ErrDenied
	}
	name := connectorName(snapshot.ID)
	metadata, err := certificateBinding(fromInstallation(snapshot))
	if err != nil {
		return Certificate{}, ErrDenied
	}
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: name},
		NotBefore:    now,
		NotAfter:     notAfter,
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:     []string{name},
		ExtraExtensions: []pkix.Extension{{
			Id:    leafBindingOID,
			Value: metadata,
		}},
	}
	der, err := x509.CreateCertificate(rand.Reader, template, i.ca, key, i.signer)
	if err != nil {
		return Certificate{}, ErrDenied
	}
	next := binding{Installation: fromInstallation(snapshot), SPKI: spki, NotAfter: notAfter}
	var original *bindingRecord
	var stored bindingRecord
	if err := i.state.Update(func(tx *store.Tx) error {
		var record bindingRecord
		err := tx.Get("installations", recordKey(resource), &record)
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return ErrDenied
		}
		if err == nil {
			if record.Resource != resource {
				return ErrDenied
			}
			copy := copyRecord(record)
			original = &copy
		} else {
			record.Resource = resource
		}
		switch {
		case original == nil:
			record.Current = next
		case sameSnapshot(record.Current.Installation, next.Installation):
			if record.Current.SPKI != next.SPKI {
				return ErrDenied
			}
			record.Current.NotAfter = next.NotAfter
		case record.Previous != nil && sameSnapshot(record.Previous.Installation, next.Installation):
			if record.Previous.SPKI != next.SPKI {
				return ErrDenied
			}
			record.Previous.NotAfter = next.NotAfter
		case next.Installation.Generation > record.Current.Installation.Generation:
			previous := record.Current
			record.Previous = &previous
			record.Current = next
		default:
			return ErrDenied
		}
		stored = copyRecord(record)
		return tx.Put("installations", recordKey(resource), stored)
	}); err != nil {
		return Certificate{}, ErrDenied
	}
	// identity.Check deliberately occurs outside the binding update. A rotation,
	// revocation, or authority reset racing issuance therefore withholds the
	// certificate and restores only the exact record this call wrote.
	if i.identity.Check(snapshot, resource) != nil {
		i.restore(resource, original, stored)
		return Certificate{}, ErrDenied
	}
	return Certificate{DER: append([]byte(nil), der...), NotAfter: notAfter}, nil
}

func (i *Issuer) restore(resource string, original *bindingRecord, stored bindingRecord) {
	_ = i.state.Update(func(tx *store.Tx) error {
		var current bindingRecord
		if tx.Get("installations", recordKey(resource), &current) != nil || !sameRecord(current, stored) {
			return nil
		}
		if original == nil {
			return tx.Delete("installations", recordKey(resource))
		}
		return tx.Put("installations", recordKey(resource), *original)
	})
}

func (i *Issuer) verifyLeaf(leaf *x509.Certificate, resource config.Resource, now time.Time) error {
	if leaf == nil || leaf.IsCA || leaf.KeyUsage != x509.KeyUsageDigitalSignature || len(leaf.ExtKeyUsage) != 1 || leaf.ExtKeyUsage[0] != x509.ExtKeyUsageServerAuth || len(leaf.UnknownExtKeyUsage) != 0 {
		return ErrDenied
	}
	name := connectorName(resource.Connector)
	if leaf.Subject.CommonName != name || !relay.ExactPeerName(leaf, name) {
		return ErrDenied
	}
	key, ok := leaf.PublicKey.(ed25519.PublicKey)
	if !ok || len(key) != ed25519.PublicKeySize {
		return ErrDenied
	}
	chains, err := leaf.Verify(x509.VerifyOptions{DNSName: name, Roots: i.roots, CurrentTime: now, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}})
	if err != nil || len(chains) == 0 || len(chains[0]) != 2 || !bytes.Equal(chains[0][1].Raw, i.ca.Raw) || leaf.CheckSignatureFrom(i.ca) != nil {
		return ErrDenied
	}
	return nil
}

// VerifyPeer validates a connector leaf independently of the TLS handshake
// verifier, then rechecks the matched identity snapshot against current local
// authority. Previous bindings survive only while identity.Check accepts the
// immediate prior JOSE generation.
func (i *Issuer) VerifyPeer(resource string, leaf *x509.Certificate) (identity.Installation, error) {
	declaration, exists := i.resources[resource]
	if !exists || leaf == nil {
		return identity.Installation{}, ErrDenied
	}
	// Verify only fields parsed from the signed DER. A supplied parsed object
	// can have mutable fields inconsistent with its RawTBSCertificate/signature.
	owned, err := x509.ParseCertificate(append([]byte(nil), leaf.Raw...))
	if err != nil {
		return identity.Installation{}, ErrDenied
	}
	leaf = owned
	var record bindingRecord
	var now time.Time
	if err := i.state.View(func(tx *store.Tx) error {
		now = tx.Now()
		if tx.Get("installations", recordKey(resource), &record) != nil || record.Resource != resource {
			return ErrDenied
		}
		return nil
	}); err != nil || i.verifyLeaf(leaf, declaration, now) != nil {
		return identity.Installation{}, ErrDenied
	}
	spki, err := x509.MarshalPKIXPublicKey(leaf.PublicKey)
	if err != nil {
		return identity.Installation{}, ErrDenied
	}
	digest := sha256.Sum256(spki)
	var matched *binding
	if record.Current.SPKI == digest && now.Before(record.Current.NotAfter) && leafBindingMatches(leaf, record.Current.Installation) {
		current := record.Current
		matched = &current
	} else if record.Previous != nil && record.Previous.SPKI == digest && now.Before(record.Previous.NotAfter) && leafBindingMatches(leaf, record.Previous.Installation) {
		previous := *record.Previous
		matched = &previous
	}
	if matched == nil || leaf.NotAfter.After(matched.NotAfter) || matched.Installation.ID != declaration.Connector || matched.Installation.Role != "connector" {
		return identity.Installation{}, ErrDenied
	}
	snapshot := matched.Installation.installation()
	if i.identity.Check(snapshot, resource) != nil {
		return identity.Installation{}, ErrDenied
	}
	return snapshot, nil
}
