package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/ingresshttp"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
)

func availableAddress(t *testing.T) string {
	t.Helper()
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	return address
}

func gatewaySettings(t *testing.T) GatewayConfig {
	t.Helper()
	file, err := os.Open("../../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	gateway, err := config.ReadGateway(file)
	file.Close()
	if err != nil {
		t.Fatal(err)
	}
	gateway.Listen = availableAddress(t)
	gateway.Resources[0].TunnelAddress = availableAddress(t)
	binary := os.Getenv("ANVIL_CONNECT_WSTUNNEL")
	if binary == "" {
		binary = "/usr/bin/wstunnel"
	}
	return GatewayConfig{Schema: "anvil-connect.gateway-runtime/v1", Gateway: gateway, ControlHost: "connect.example.test", TunnelHost: "tunnel.example.test", StateDirectory: filepath.Join(t.TempDir(), "gateway"), TunnelBinary: binary, TunnelListen: availableAddress(t)}
}

func TestGatewayInitializationIsExclusiveAndPreservesAuthorities(t *testing.T) {
	c := gatewaySettings(t)
	if err := InitializeGateway(c); err != nil {
		t.Fatal(err)
	}
	directory, err := privatefiles.Open(c.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	before, err := directory.Read("authorities.json", 32768)
	if err != nil {
		t.Fatal(err)
	}
	inner, tunnel, err := loadAuthorities(directory)
	if err != nil || bytes.Equal(inner.certificate.Raw, tunnel.certificate.Raw) {
		t.Fatal("authorities were not independently generated", err)
	}
	if err := InitializeGateway(c); err == nil {
		t.Fatal("existing private authority was overwritten")
	}
	after, err := directory.Read("authorities.json", 32768)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("failed initialization changed private state")
	}
	if _, err := os.Stat(filepath.Join(c.StateDirectory, "authority", "state.db")); !os.IsNotExist(err) {
		t.Fatal("initialization unexpectedly started authority service")
	}
}

func TestStartGatewayRequiresDeclaredIngressBeforeState(t *testing.T) {
	c := gatewaySettings(t)
	if c.Ingress != nil {
		t.Fatal("same-UID test declaration unexpectedly has managed ingress")
	}
	if gateway, err := StartGateway(context.Background(), c, func(string) (string, bool) { return "", false }); err == nil || gateway != nil {
		t.Fatal("legacy same-UID declaration started")
	}
	if _, err := os.Lstat(c.StateDirectory); !os.IsNotExist(err) {
		t.Fatal("legacy same-UID declaration created gateway state")
	}
}

func TestProtocolFixtureComposeRejectsManagedIngress(t *testing.T) {
	c := gatewaySettings(t)
	c.Ingress = &ingresshttp.Policy{GatewayUID: 1001, EdgeUID: 1002, GroupID: 1003, Directory: "/run/anvil-connect/ingress"}
	if c.Ingress.Validate() != nil {
		t.Fatal("managed ingress test policy is invalid")
	}
	if gateway, err := ComposeGatewayForProtocolFixture(context.Background(), c, func(string) (string, bool) { return "", false }); err == nil || gateway != nil {
		t.Fatal("protocol fixture compose accepted managed ingress")
	}
	if _, err := os.Lstat(c.StateDirectory); !os.IsNotExist(err) {
		t.Fatal("protocol fixture compose created gateway state")
	}
}

func TestNativeDeclarationsRejectAmbiguityBeforeStartup(t *testing.T) {
	c := gatewaySettings(t)
	data, err := json.Marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	for _, corrupt := range [][]byte{
		bytes.Replace(data, []byte(`"control_host":`), []byte(`"Control_Host":`), 1),
		bytes.Replace(data, []byte(`"control_host":`), []byte(`"control_host":"other.example.test","control_host":`), 1),
		bytes.Replace(data, []byte(`"oidc":{`), []byte(`"password":"literal","oidc":{`), 1),
		append(append([]byte(nil), data...), []byte(` {}`)...),
	} {
		if _, err := ReadGateway(bytes.NewReader(corrupt)); err == nil {
			t.Fatal("ambiguous declaration accepted")
		}
	}
	c.TunnelHost = c.Gateway.Resources[0].Rule.Host
	if err := InitializeGateway(c); err == nil {
		t.Fatal("overlapping host binding accepted")
	}
	if _, err := os.Stat(c.StateDirectory); !os.IsNotExist(err) {
		t.Fatal("invalid declaration touched private state")
	}
}

func unixClient(t *testing.T, directory *privatefiles.Directory, name string) *http.Client {
	t.Helper()
	pin, err := directory.PinPath(name)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = pin.Close() })
	protocols := new(http.Protocols)
	protocols.SetUnencryptedHTTP2(true)
	tr := &http.Transport{Protocols: protocols, Proxy: nil, DisableKeepAlives: true, DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", pin.Path())
	}}
	t.Cleanup(tr.CloseIdleConnections)
	return &http.Client{Transport: tr, Timeout: 3 * time.Second}
}

// This is protocol-only coverage for the historical same-UID ingress model.
// Managed activation must use StartGateway and the declared cross-UID policy.
func TestGatewayOwnedLifecycleAndSeparateAdminIngress(t *testing.T) {
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
	adminPath, err := directory.PinPath("admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer adminPath.Close()
	client := unixClient(t, directory, "ingress.sock")
	var epoch string
	for generation := range 2 {
		gateway, err := ComposeGatewayForProtocolFixture(context.Background(), c, func(string) (string, bool) { t.Error("API-only gateway read an OIDC secret"); return "", false })
		if err != nil {
			t.Fatal(err)
		}
		// Ensure every failure path still releases owned children and listeners.
		func() {
			defer gateway.Close()
			status, err := admin.Call(context.Background(), adminPath.Path(), admin.Request{Operation: "status"})
			if err != nil || len(status.Epoch) != 64 {
				t.Fatal("admin did not use live gateway authority", err)
			}
			if generation == 0 {
				epoch = status.Epoch
			} else if status.Epoch != epoch {
				t.Fatal("normal restart reset identity authority")
			}
			grants := []access.Grant{{Resource: "router", Methods: []string{"GET"}}}
			if _, err := admin.Call(context.Background(), adminPath.Path(), admin.Request{Operation: "principal-set", Principal: "sdk", Grants: grants}); err != nil {
				t.Fatal(err)
			}
			issued, err := admin.Call(context.Background(), adminPath.Path(), admin.Request{Operation: "api-key-issue", Principal: "sdk", Grants: grants, LifetimeSeconds: 60})
			if err != nil || issued.Secret == "" {
				t.Fatal("key issue failed", err)
			}
			for _, host := range []string{admin.Host, c.Gateway.Resources[0].Rule.Host} {
				request, _ := http.NewRequest("GET", "http://"+host+"/v1/models", nil)
				request.Header.Set("Authorization", "Bearer "+issued.Secret)
				response, err := client.Do(request)
				if err != nil {
					t.Fatal(err)
				}
				_, _ = io.Copy(io.Discard, response.Body)
				response.Body.Close()
				expected := 502 // no connector is enrolled/running in this fixture.
				if host == admin.Host {
					expected = 404
				}
				if response.ProtoMajor != 2 || response.StatusCode != expected {
					t.Fatal("Unix H2 ingress or admin separation failed", response.StatusCode, response.Proto)
				}
			}
		}()
		for _, name := range []string{"admin.sock", "ingress.sock"} {
			if _, err := os.Lstat(filepath.Join(c.StateDirectory, name)); !os.IsNotExist(err) {
				t.Fatal("owned socket remained after close", err)
			}
		}
	}
}

// This is protocol-only coverage for the historical same-UID ingress model.
func TestGatewayStartupFailurePreservesUnownedSocketName(t *testing.T) {
	if os.Getenv("ANVIL_CONNECT_WSTUNNEL") == "" {
		t.Skip("requires explicit pinned transport artifact")
	}
	c := gatewaySettings(t)
	if err := InitializeGateway(c); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(c.StateDirectory, "ingress.sock")
	if err := os.WriteFile(path, []byte("unowned marker"), 0600); err != nil {
		t.Fatal(err)
	}
	if gateway, err := ComposeGatewayForProtocolFixture(context.Background(), c, func(string) (string, bool) { return "", false }); err == nil {
		gateway.Close()
		t.Fatal("occupied socket was replaced")
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "unowned marker" {
		t.Fatal("unowned entry changed", err)
	}
	if _, err := os.Lstat(filepath.Join(c.StateDirectory, "admin.sock")); !os.IsNotExist(err) {
		t.Fatal("partial startup leaked admin listener", err)
	}
}

func TestIdentitySignersRequireExactDeclaredSecrets(t *testing.T) {
	c := gatewaySettings(t)
	resource := &c.Gateway.Resources[0]
	resource.Rule.Access = "browser"
	resource.Rule.NativeAuth = "signed-identity"
	resource.IdentityKeyEnv = "ANVIL_CONNECT_DASH_IDENTITY_KEY"
	resource.IdentityKeyID = "dash-v1"
	if c.Gateway.Validate() != nil {
		t.Fatal("invalid signed identity test declaration")
	}
	for name, lookup := range map[string]SecretSource{
		"missing":   func(string) (string, bool) { return "", false },
		"malformed": func(string) (string, bool) { return "not-a-key", true },
		"wrong-name": func(name string) (string, bool) {
			return "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8", name == "OTHER"
		},
	} {
		t.Run(name, func(t *testing.T) {
			if signers, err := identitySigners(c.Gateway.Resources, lookup); err == nil || signers != nil {
				t.Fatal("invalid identity signer input was accepted")
			}
		})
	}
	signers, err := identitySigners(c.Gateway.Resources, func(name string) (string, bool) {
		return "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8", name == resource.IdentityKeyEnv
	})
	if err != nil || len(signers) != 1 || signers[resource.Rule.ID] == nil {
		t.Fatal("declared identity signer was unavailable")
	}
}
