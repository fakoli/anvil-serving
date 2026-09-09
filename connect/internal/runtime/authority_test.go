package runtime

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"path/filepath"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
)

func TestLoadAuthoritiesRejectsSharedPublicKey(t *testing.T) {
	directory, err := privatefiles.Open(filepath.Join(t.TempDir(), "state"))
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	_, signer, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	inner := selfSignedBundle(t, signer, "inner")
	tunnel := selfSignedBundle(t, signer, "tunnel")
	if inner == tunnel {
		t.Fatal("test setup produced identical authority bundles")
	}
	data, err := json.Marshal(authorities{Schema: "anvil-connect.authorities/v1", Inner: inner, Tunnel: tunnel})
	if err != nil {
		t.Fatal(err)
	}
	if err := directory.Create("authorities.json", data); err != nil {
		t.Fatal(err)
	}
	if _, _, err := loadAuthorities(directory); err == nil {
		t.Fatal("shared authority public key accepted")
	}
}

// selfSignedBundle makes distinct certificate DER while deliberately reusing
// signer. loadAuthorities must reject this even though the old raw-certificate
// comparison would have accepted it.
func selfSignedBundle(t *testing.T, signer ed25519.PrivateKey, name string) string {
	t.Helper()
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	template := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "test " + name},
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign,
		MaxPathLen:            0,
		MaxPathLenZero:        true,
		NotBefore:             now.Add(-time.Minute),
		NotAfter:              now.Add(time.Hour),
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, signer.Public(), signer)
	if err != nil {
		t.Fatal(err)
	}
	key, err := x509.MarshalPKCS8PrivateKey(signer)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})) + string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: key}))
}
