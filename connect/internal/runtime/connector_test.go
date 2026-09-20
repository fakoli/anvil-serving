package runtime

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	stdruntime "runtime"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/access"
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

func TestReenrollConnectorStagesExactSupersetWithoutReplacingImmutableInit(t *testing.T) {
	old, initial := connectorFixture(t)
	state, err := newConnectorState(old, initial)
	if err != nil {
		t.Fatal(err)
	}
	state.Status, state.Invitation = "enrolled", ""
	directory, err := privatefiles.Open(old.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(state)
	if err != nil || directory.Create("installation.json", encoded) != nil {
		directory.Close()
		t.Fatal("could not retain enrolled connector state")
	}
	directory.Close()
	prior := ConnectorPrior{ID: state.ID, Fingerprint: state.Fingerprint, Epoch: state.Epoch, Generation: state.Generation, Resources: append([]string(nil), state.Resources...)}

	next := old
	next.Resources = append(next.Resources, ConnectorResource{Envelope: config.Envelope{Rule: config.Rule{ID: "metrics", Host: "metrics.example.test", PathPrefix: "/", Methods: []string{"GET"}, Access: "api", NativeAuth: "delegate-bearer", Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 1}}, Listen: "127.0.0.1:18081", OriginURL: "http://127.0.0.1:19081", TokenEnv: "ANVIL_CONNECT_METRICS_TOKEN"}, ReverseAddress: "127.0.0.1:18181"})
	bundle := initial
	bundle.Generation++
	bundle.Resources = []string{"metrics", "router"}

	before, err := os.ReadFile(filepath.Join(old.StateDirectory, "installation.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := InitializeConnector(context.Background(), next, bundle); err == nil {
		t.Fatal("ordinary init accepted a valid expanded declaration over retained old state")
	}
	afterImmutableInit, err := os.ReadFile(filepath.Join(old.StateDirectory, "installation.json"))
	if err != nil || !bytes.Equal(before, afterImmutableInit) {
		t.Fatal("ordinary immutable init changed retained state")
	}

	for name, mutate := range map[string]func(*ConnectorPrior, *admin.Response, *ConnectorConfig){
		"fingerprint": func(p *ConnectorPrior, _ *admin.Response, _ *ConnectorConfig) {
			p.Fingerprint = strings.Repeat("A", 43)
		},
		"generation":   func(p *ConnectorPrior, _ *admin.Response, _ *ConnectorConfig) { p.Generation++ },
		"resources":    func(p *ConnectorPrior, _ *admin.Response, _ *ConnectorConfig) { p.Resources = []string{"metrics"} },
		"bundle epoch": func(_ *ConnectorPrior, b *admin.Response, _ *ConnectorConfig) { b.Epoch = strings.Repeat("f", 64) },
		"bundle CA": func(_ *ConnectorPrior, b *admin.Response, _ *ConnectorConfig) {
			b.InnerCAPEM = string(testpki.New(t).PEM())
		},
		"not superset": func(_ *ConnectorPrior, b *admin.Response, c *ConnectorConfig) {
			b.Resources = []string{"router"}
			c.Resources = c.Resources[:1]
		},
	} {
		t.Run(name, func(t *testing.T) {
			gotPrior, gotBundle, gotConfig := prior, bundle, next
			gotPrior.Resources = append([]string(nil), prior.Resources...)
			gotBundle.Resources = append([]string(nil), bundle.Resources...)
			gotConfig.Resources = append([]ConnectorResource(nil), next.Resources...)
			mutate(&gotPrior, &gotBundle, &gotConfig)
			if ReenrollConnector(gotConfig, gotPrior, gotBundle) == nil {
				t.Fatal("drift accepted")
			}
			after, err := os.ReadFile(filepath.Join(old.StateDirectory, "installation.json"))
			if err != nil || !bytes.Equal(before, after) {
				t.Fatal("drift mutated retained state")
			}
		})
	}

	if err := ReenrollConnector(next, prior, bundle); err != nil {
		t.Fatalf("stage replacement: %v", err)
	}
	directory, err = privatefiles.Open(next.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	staged, exists, err := loadConnectorState(directory)
	if err != nil || !exists || staged.Status != "pending" || staged.Invitation != bundle.Invitation || staged.Generation != bundle.Generation || !sameResources(staged.Resources, bundle.Resources) || staged.Fingerprint == prior.Fingerprint {
		t.Fatalf("staged replacement = %#v exists=%v err=%v", staged, exists, err)
	}
	backup, err := directory.Read(retainedConnectorStateName(prior.Generation), 256*1024)
	if err != nil {
		t.Fatal("old connector state was not retained")
	}
	var retained connectorState
	if config.Decode(bytes.NewReader(backup), &retained) != nil || !matchesConnectorPrior(retained, old, prior) {
		t.Fatal("retained connector state does not match prior binding")
	}
	stagedBytes, err := os.ReadFile(filepath.Join(next.StateDirectory, "installation.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := ReenrollConnector(next, prior, bundle); err != nil {
		t.Fatalf("exact staged retry: %v", err)
	}
	retried, err := os.ReadFile(filepath.Join(next.StateDirectory, "installation.json"))
	if err != nil || !bytes.Equal(stagedBytes, retried) {
		t.Fatal("staged retry changed pending replacement")
	}
}

func TestReenrollCLIReadsRoleOwnedPrivateInputs(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("exercise this ownership contract as the dedicated service user")
	}
	old, initial := connectorFixture(t)
	state, err := newConnectorState(old, initial)
	if err != nil {
		t.Fatal(err)
	}
	state.Status, state.Invitation = "enrolled", ""
	directory, err := privatefiles.Open(old.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(state)
	if err != nil || directory.Create("installation.json", encoded) != nil {
		directory.Close()
		t.Fatal("could not retain enrolled connector state")
	}
	directory.Close()
	next := old
	next.Resources = append(next.Resources, ConnectorResource{Envelope: config.Envelope{Rule: config.Rule{ID: "metrics", Host: "metrics.example.test", PathPrefix: "/", Methods: []string{"GET"}, Access: "api", NativeAuth: "delegate-bearer", Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 1}}, Listen: "127.0.0.1:18081", OriginURL: "http://127.0.0.1:19081", TokenEnv: "ANVIL_CONNECT_METRICS_TOKEN"}, ReverseAddress: "127.0.0.1:18181"})
	bundle := initial
	bundle.Generation++
	bundle.Resources = []string{"metrics", "router"}
	// The local-admin invite response initializes all closed empty arrays.
	bundle.Grants = []access.Grant{}
	bundle.Status.Resources = []string{}
	prior := ConnectorPrior{ID: state.ID, Fingerprint: state.Fingerprint, Epoch: state.Epoch, Generation: state.Generation, Resources: append([]string(nil), state.Resources...)}

	parent := filepath.Join(t.TempDir(), "extend")
	input := filepath.Join(parent, "connector-a")
	if err := os.Mkdir(parent, 0730); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(input, 0700); err != nil {
		t.Fatal(err)
	}
	priorData, err := json.Marshal(prior)
	if err != nil {
		t.Fatal(err)
	}
	bundleData, err := json.Marshal(bundle)
	if err != nil {
		t.Fatal(err)
	}
	priorPath, bundlePath := filepath.Join(input, "prior.json"), filepath.Join(input, "invite.json")
	if err := os.WriteFile(priorPath, priorData, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(bundlePath, bundleData, 0600); err != nil {
		t.Fatal(err)
	}
	declaration, err := json.Marshal(next)
	if err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(t.TempDir(), "connector.json")
	if err := os.WriteFile(configPath, declaration, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadConnector(bytes.NewReader(declaration)); err != nil {
		t.Fatalf("test connector declaration invalid: %v", err)
	}
	var decodedPrior ConnectorPrior
	var decodedBundle admin.Response
	if err := config.Decode(bytes.NewReader(priorData), &decodedPrior); err != nil {
		t.Fatalf("test prior is not closed native JSON: %v", err)
	}
	if err := config.Decode(bytes.NewReader(bundleData), &decodedBundle); err != nil {
		t.Fatalf("test bundle is not closed native JSON: %v", err)
	}
	_, source, _, ok := stdruntime.Caller(0)
	if !ok {
		t.Fatal("runtime source unavailable")
	}
	binary := filepath.Join(t.TempDir(), "anvil-connect")
	build := exec.Command("go", "build", "-o", binary, "./cmd/anvil-connect")
	build.Dir = filepath.Clean(filepath.Join(filepath.Dir(source), "../.."))
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build native CLI: %v: %s", err, output)
	}
	result := exec.Command(binary, "re-enroll", "--config", configPath, "--prior", priorPath, "--bundle", bundlePath)
	output, err := result.CombinedOutput()
	if err != nil || !bytes.Contains(output, []byte(`"status":"replacement-staged"`)) {
		t.Fatalf("role-owned input re-enroll: %v: %s", err, output)
	}
	directory, err = privatefiles.Open(next.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	staged, exists, err := loadConnectorState(directory)
	if err != nil || !exists || staged.Status != "pending" || !sameResources(staged.Resources, bundle.Resources) {
		t.Fatalf("native CLI did not stage replacement: %#v exists=%v err=%v", staged, exists, err)
	}
}
