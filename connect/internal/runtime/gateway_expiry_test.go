package runtime

import (
	"context"
	"crypto/rand"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
)

// Exercise the real certificate-derived timer with a short-lived test CA.
// Production leaf lifetime and the system clock remain unchanged. This proves
// the supervisor failure signal in the protocol-only same-UID fixture, not a
// full-day live rotation/reconnect soak.
func TestGatewayCertificateBoundarySignalsSupervisorFailure(t *testing.T) {
	if os.Getenv("ANVIL_CONNECT_WSTUNNEL") == "" {
		t.Skip("requires explicit pinned transport artifact")
	}
	c := gatewaySettings(t)
	if err := InitializeGateway(c); err != nil {
		t.Fatal(err)
	}
	directory, err := privatefiles.Open(c.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	raw, err := directory.Read("authorities.json", 32768)
	if err != nil {
		t.Fatal(err)
	}
	var bundles authorities
	if err := json.Unmarshal(raw, &bundles); err != nil {
		t.Fatal(err)
	}
	inner, err := parseCA(bundles.Inner)
	if err != nil {
		t.Fatal(err)
	}
	template := *inner.certificate
	template.NotAfter = time.Now().Add(time.Hour + 7*time.Second).Truncate(time.Second)
	der, err := x509.CreateCertificate(rand.Reader, &template, &template, inner.private.Public(), inner.private)
	if err != nil {
		t.Fatal(err)
	}
	_, key := pem.Decode([]byte(bundles.Inner))
	bundles.Inner = string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})) + string(key)
	raw, err = json.Marshal(bundles)
	if err != nil || directory.Replace("authorities.json", raw) != nil {
		t.Fatal("could not prepare bounded test authority")
	}
	gateway, err := ComposeGatewayForProtocolFixture(context.Background(), c, func(string) (string, bool) {
		t.Error("API-only gateway read an OIDC secret")
		return "", false
	})
	if err != nil {
		t.Fatal(err)
	}
	defer gateway.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()
	if err := gateway.Wait(ctx); !errors.Is(err, ErrUnavailable) {
		t.Fatal("certificate boundary did not return supervisor failure", err)
	}
	if time.Now().Before(template.NotAfter.Add(-time.Hour)) {
		t.Fatal("gateway failed before its certificate restart boundary")
	}
	gateway.Close()
	for _, name := range []string{"admin.sock", "ingress.sock"} {
		if _, err := os.Lstat(filepath.Join(c.StateDirectory, name)); !os.IsNotExist(err) {
			t.Fatal("certificate shutdown leaked an owned socket", err)
		}
	}
}
