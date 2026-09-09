package runtime

import (
	"bytes"
	"context"
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

func connectorFixture(t *testing.T) (ConnectorConfig, admin.Response) {
	t.Helper()
	ca := testpki.New(t)
	trust := filepath.Join(t.TempDir(), "public-roots.pem")
	if err := os.WriteFile(trust, ca.PEM(), 0644); err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(t.TempDir(), "state")
	rule := config.Rule{ID: "router", Host: "router.example.test", PathPrefix: "/", Methods: []string{"GET"}, Access: "api", NativeAuth: "delegate-bearer", Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 1}}
	c := ConnectorConfig{Schema: "anvil-connect.connector-runtime/v1", ID: "connector-a", ControlHost: "control.example.test", TunnelHost: "tunnel.example.test", StateDirectory: state, TunnelBinary: "/usr/bin/true", PublicTrustFile: trust, Resources: []ConnectorResource{{Envelope: config.Envelope{Rule: rule, Listen: "127.0.0.1:18080", OriginURL: "http://127.0.0.1:19080", TokenEnv: "ANVIL_CONNECT_TOKEN"}, ReverseAddress: "127.0.0.1:18180"}}}
	b := admin.Response{Operation: "invite", Invitation: "aci1." + strings.Repeat("0", 32) + "." + strings.Repeat("A", 43), Installation: c.ID, Role: "connector", Resources: []string{"router"}, Epoch: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", Generation: 1, ControlHost: c.ControlHost, TunnelHost: c.TunnelHost, InnerCAPEM: string(ca.PEM())}
	return c, b
}

func TestInitializeConnectorPersistsDistinctPendingKeysBeforeEnrollment(t *testing.T) {
	c, bundle := connectorFixture(t)
	malformed := bundle
	malformed.Invitation = "aci1.invalid"
	if err := InitializeConnector(context.Background(), c, malformed); err == nil {
		t.Fatal("malformed invitation accepted")
	}
	if _, err := os.Stat(filepath.Join(c.StateDirectory, "installation.json")); !os.IsNotExist(err) {
		t.Fatalf("malformed bundle created state: %v", err)
	}
	wrong := bundle
	// Keep the public invitation ID and every bundle binding unchanged. Only the
	// bearer secret differs, as it would when an operator corrects a mistyped
	// invitation after the first enrollment request could not complete.
	wrong.Invitation = "aci1." + strings.Repeat("0", 32) + "." + strings.Repeat("B", 43)
	if err := InitializeConnector(context.Background(), c, wrong); err == nil {
		t.Fatal("unreachable wrong invitation unexpectedly succeeded")
	}
	if err := InitializeConnector(context.Background(), c, bundle); err == nil {
		t.Fatal("unreachable corrected invitation unexpectedly succeeded")
	}
	directory, err := privatefiles.Open(c.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	state, exists, err := loadConnectorState(directory)
	if err != nil || !exists || state.Status != "pending" || state.Invitation != bundle.Invitation {
		t.Fatalf("pending state = %#v exists=%v err=%v", state, exists, err)
	}
	private, err := base64.RawURLEncoding.DecodeString(state.PrivateKey)
	if err != nil {
		t.Fatal(err)
	}
	for _, resource := range state.ResourceKeys {
		tlsPrivate, err := base64.RawURLEncoding.DecodeString(resource.TLSPrivate)
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Equal(private[32:], tlsPrivate[32:]) {
			t.Fatal("JOSE and TLS keys reused")
		}
	}
}
