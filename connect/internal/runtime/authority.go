package runtime

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
)

type authorities struct {
	Schema string `json:"schema"`
	Inner  string `json:"inner"`
	Tunnel string `json:"tunnel"`
}

type certificateAuthority struct {
	certificate *x509.Certificate
	private     ed25519.PrivateKey
	roots       *x509.CertPool
}

func randomHex() (string, error) {
	var value [32]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", ErrConfiguration
	}
	return hex.EncodeToString(value[:]), nil
}

func newCA() (string, error) {
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return "", ErrConfiguration
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return "", ErrConfiguration
	}
	now := time.Now()
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: "Anvil Connect private authority"}, IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign, MaxPathLen: 0, MaxPathLenZero: true, NotBefore: now.Add(-time.Minute), NotAfter: now.AddDate(5, 0, 0)}
	der, err := x509.CreateCertificate(rand.Reader, template, template, public, private)
	if err != nil {
		return "", ErrConfiguration
	}
	key, err := x509.MarshalPKCS8PrivateKey(private)
	if err != nil {
		return "", ErrConfiguration
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})) + string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: key})), nil
}

func parseCA(bundle string) (certificateAuthority, error) {
	certBlock, rest := pem.Decode([]byte(bundle))
	keyBlock, rest := pem.Decode(rest)
	if certBlock == nil || keyBlock == nil || certBlock.Type != "CERTIFICATE" || keyBlock.Type != "PRIVATE KEY" || len(certBlock.Headers) != 0 || len(keyBlock.Headers) != 0 || len(bytes.TrimSpace(rest)) != 0 {
		return certificateAuthority{}, ErrConfiguration
	}
	certificate, err := x509.ParseCertificate(certBlock.Bytes)
	if err != nil || !certificate.IsCA || !certificate.BasicConstraintsValid || certificate.KeyUsage != x509.KeyUsageCertSign || certificate.CheckSignatureFrom(certificate) != nil || !time.Now().Before(certificate.NotAfter) || time.Now().Before(certificate.NotBefore) {
		return certificateAuthority{}, ErrConfiguration
	}
	key, err := x509.ParsePKCS8PrivateKey(keyBlock.Bytes)
	private, ok := key.(ed25519.PrivateKey)
	if err != nil || !ok {
		return certificateAuthority{}, ErrConfiguration
	}
	public, err := x509.MarshalPKIXPublicKey(private.Public())
	if err != nil || !bytes.Equal(public, certificate.RawSubjectPublicKeyInfo) {
		return certificateAuthority{}, ErrConfiguration
	}
	roots := x509.NewCertPool()
	roots.AddCert(certificate)
	return certificateAuthority{certificate, private, roots}, nil
}

func (ca certificateAuthority) leaf(name, commonName string, client bool) (tls.Certificate, error) {
	if !config.ValidHost(name) {
		return tls.Certificate{}, ErrConfiguration
	}
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return tls.Certificate{}, ErrConfiguration
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return tls.Certificate{}, ErrConfiguration
	}
	now := time.Now()
	until := now.Add(24 * time.Hour)
	if ca.certificate.NotAfter.Before(until) {
		until = ca.certificate.NotAfter
	}
	if !until.After(now.Add(time.Hour)) {
		return tls.Certificate{}, ErrConfiguration
	}
	usage := x509.ExtKeyUsageServerAuth
	if client {
		usage = x509.ExtKeyUsageClientAuth
	}
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: commonName}, DNSNames: []string{name}, NotBefore: now.Add(-time.Minute), NotAfter: until, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{usage}}
	der, err := x509.CreateCertificate(rand.Reader, template, ca.certificate, public, ca.private)
	if err != nil {
		return tls.Certificate{}, ErrConfiguration
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: private}, nil
}

func certificatePEM(certificate tls.Certificate) ([]byte, []byte, error) {
	if len(certificate.Certificate) != 1 || certificate.PrivateKey == nil {
		return nil, nil, ErrConfiguration
	}
	key, err := x509.MarshalPKCS8PrivateKey(certificate.PrivateKey)
	if err != nil {
		return nil, nil, ErrConfiguration
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificate.Certificate[0]}), pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: key}), nil
}

// InitializeGateway creates independent private authorities once, atomically in
// one owned file. It refuses to replace existing state; start never initializes
// missing keys or silently creates a replacement deployment identity.
func InitializeGateway(declaration GatewayConfig) error {
	if declaration.Validate() != nil {
		return ErrConfiguration
	}
	directory, err := privatefiles.Open(declaration.StateDirectory)
	if err != nil {
		return ErrConfiguration
	}
	defer directory.Close()
	inner, err := newCA()
	if err != nil {
		return err
	}
	tunnel, err := newCA()
	if err != nil {
		return err
	}
	data, err := json.Marshal(authorities{Schema: "anvil-connect.authorities/v1", Inner: inner, Tunnel: tunnel})
	if err != nil || directory.Create("authorities.json", data) != nil {
		return ErrConfiguration
	}
	return nil
}

func loadAuthorities(directory *privatefiles.Directory) (certificateAuthority, certificateAuthority, error) {
	data, err := directory.Read("authorities.json", 32768)
	var settings authorities
	if err != nil || config.Decode(bytes.NewReader(data), &settings) != nil || settings.Schema != "anvil-connect.authorities/v1" {
		return certificateAuthority{}, certificateAuthority{}, ErrConfiguration
	}
	inner, err := parseCA(settings.Inner)
	if err != nil {
		return certificateAuthority{}, certificateAuthority{}, err
	}
	tunnel, err := parseCA(settings.Tunnel)
	if err != nil {
		return certificateAuthority{}, certificateAuthority{}, ErrConfiguration
	}
	innerSPKI, innerErr := x509.MarshalPKIXPublicKey(inner.certificate.PublicKey)
	tunnelSPKI, tunnelErr := x509.MarshalPKIXPublicKey(tunnel.certificate.PublicKey)
	if innerErr != nil || tunnelErr != nil || bytes.Equal(innerSPKI, tunnelSPKI) {
		return certificateAuthority{}, certificateAuthority{}, ErrConfiguration
	}
	return inner, tunnel, nil
}
