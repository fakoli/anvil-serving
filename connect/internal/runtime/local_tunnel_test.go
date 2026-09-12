package runtime

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
)

func localTunnelSettings(t *testing.T) (GatewayConfig, ConnectorConfig) {
	t.Helper()
	raw, err := os.ReadFile("../../examples/deployment.json")
	if err != nil {
		t.Fatal(err)
	}
	var deployment struct {
		Gateway    GatewayConfig
		Connectors []ConnectorConfig
	}
	if err := json.Unmarshal(raw, &deployment); err != nil {
		t.Fatal(err)
	}
	g, c := deployment.Gateway, deployment.Connectors[0]
	g.LocalTunnel = &LocalTunnelListener{Listen: "127.0.0.1:18443", ServerName: "local-tls.example.test", HTTPHost: "local-http.example.test", CertificateFile: "/etc/connect-local/leaf.pem", PrivateKeyFile: "/etc/connect-local/leaf.key", TrustFile: "/etc/connect-local/root.pem"}
	c.LocalTunnel = &LocalTunnelEndpoint{Address: g.LocalTunnel.Listen, ServerName: g.LocalTunnel.ServerName, HTTPHost: g.LocalTunnel.HTTPHost, TrustFile: "/etc/connector-local/root.pem"}
	return g, c
}

func TestLocalTunnelRoundTripAndOmission(t *testing.T) {
	g, c := localTunnelSettings(t)
	for _, port := range []string{"1", "18443", "65535"} {
		g.LocalTunnel.Listen, c.LocalTunnel.Address = "127.0.0.1:"+port, "127.0.0.1:"+port
		if err := c.ValidateGateway(g); err != nil {
			t.Fatal(err)
		}
		for _, declaration := range []any{g, c} {
			raw, err := json.Marshal(declaration)
			if err != nil {
				t.Fatal(err)
			}
			var decoded any
			if _, ok := declaration.(GatewayConfig); ok {
				decoded, err = ReadGateway(bytes.NewReader(raw))
			} else {
				decoded, err = ReadConnector(bytes.NewReader(raw))
			}
			if err != nil {
				t.Fatal(err)
			}
			again, err := json.Marshal(decoded)
			if err != nil || !bytes.Equal(raw, again) {
				t.Fatal("local declaration changed on round trip", err)
			}
		}
	}
	c.LocalTunnel = nil // Staged listener, public connector.
	if err := c.ValidateGateway(g); err != nil {
		t.Fatal(err)
	}
	g.LocalTunnel = nil
	for _, declaration := range []any{g, c} {
		raw, _ := json.Marshal(declaration)
		if bytes.Contains(raw, []byte("local_tunnel")) {
			t.Fatal("omission inserted a local default")
		}
	}
}

func TestLocalTunnelClosedReaders(t *testing.T) {
	g, c := localTunnelSettings(t)
	for _, declaration := range []any{g, c} {
		raw, _ := json.Marshal(declaration)
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(raw, &fields); err != nil {
			t.Fatal(err)
		}
		local := fields["local_tunnel"]
		var object map[string]any
		if err := json.Unmarshal(local, &object); err != nil {
			t.Fatal(err)
		}
		malformed := []string{"null", "{}", "[]", `""`, "false", "1"}
		for key := range object {
			for _, value := range []any{nil, "", false, 1} {
				copy := map[string]any{}
				for k, v := range object {
					copy[k] = v
				}
				copy[key] = value
				encoded, _ := json.Marshal(copy)
				malformed = append(malformed, string(encoded))
			}
			copy := map[string]any{}
			for k, v := range object {
				if k != key {
					copy[k] = v
				}
			}
			encoded, _ := json.Marshal(copy)
			malformed = append(malformed, string(encoded))
			malformed = append(malformed, strings.Replace(string(local), `"`+key+`":`, `"`+strings.ToUpper(key)+`":`, 1))
			malformed = append(malformed, strings.Replace(string(local), `"`+key+`":`, `"`+key+`":"duplicate","`+key+`":`, 1))
		}
		for _, key := range []string{"proxy", "http_proxy_url", "enabled", "path", "unknown"} {
			malformed = append(malformed, strings.TrimSuffix(string(local), "}")+`,"`+key+`":"forbidden"}`)
		}
		for _, replacement := range malformed {
			corrupt := bytes.Replace(raw, local, []byte(replacement), 1)
			var err error
			if _, ok := declaration.(GatewayConfig); ok {
				_, err = ReadGateway(bytes.NewReader(corrupt))
			} else {
				_, err = ReadConnector(bytes.NewReader(corrupt))
			}
			if !errors.Is(err, ErrConfiguration) {
				t.Fatalf("accepted malformed local object %s: %v", replacement, err)
			}
		}
		for _, corrupt := range [][]byte{
			bytes.Replace(raw, []byte(`"local_tunnel":`), []byte(`"Local_Tunnel":`), 1),
			bytes.Replace(raw, []byte(`"local_tunnel":`), append(append([]byte(`"local_tunnel":`), local...), []byte(`,"local_tunnel":`)...), 1),
		} {
			var err error
			if _, ok := declaration.(GatewayConfig); ok {
				_, err = ReadGateway(bytes.NewReader(corrupt))
			} else {
				_, err = ReadConnector(bytes.NewReader(corrupt))
			}
			if !errors.Is(err, ErrConfiguration) {
				t.Fatal("accepted duplicate/cased declaration", err)
			}
		}
	}
}

func TestLocalTunnelValidation(t *testing.T) {
	g, c := localTunnelSettings(t)
	for _, address := range []string{"localhost:443", "0.0.0.0:443", "127.0.0.2:443", "[::1]:443", "127.1:443", "127.0.0.1:0", "127.0.0.1:65536", "127.0.0.1:0443", "127.0.0.1:+443", "https://127.0.0.1:443", "user@127.0.0.1:443", "127.0.0.1:443?q", "127.0.0.1:443#f"} {
		g.LocalTunnel.Listen, c.LocalTunnel.Address = address, address
		for _, err := range []error{g.Validate(), c.Validate()} {
			if err == nil || !strings.Contains(err.Error(), "127.0.0.1 TCP address") {
				t.Fatalf("address %q: %v", address, err)
			}
		}
	}
	for _, host := range []string{"LOCAL.example.test", "localhost", "127.0.0.1", "bad..example.test", "*.example.test", "local.example.test.", "local.example.test:443", "https://local.example.test", "-bad.example.test"} {
		for _, field := range []string{"server_name", "http_host"} {
			g, c = localTunnelSettings(t)
			if field == "server_name" {
				g.LocalTunnel.ServerName, c.LocalTunnel.ServerName = host, host
			} else {
				g.LocalTunnel.HTTPHost, c.LocalTunnel.HTTPHost = host, host
			}
			for _, err := range []error{g.Validate(), c.Validate()} {
				if err == nil || !strings.Contains(err.Error(), "lower-case DNS host") {
					t.Fatalf("host %q: %v", host, err)
				}
			}
		}
	}
	for _, path := range []string{"relative.pem", "/", "//etc/root.pem", "/etc/../root.pem", "/etc/./root.pem", "/etc//root.pem", "/etc/root.pem/", "/etc/root\n.pem"} {
		g, c = localTunnelSettings(t)
		g.LocalTunnel.TrustFile, c.LocalTunnel.TrustFile = path, path
		for _, err := range []error{g.Validate(), c.Validate()} {
			if err == nil || !strings.Contains(err.Error(), "clean absolute path") {
				t.Fatalf("path %q: %v", path, err)
			}
		}
	}
	mutations := map[string]func(*GatewayConfig, *ConnectorConfig){
		"missing gateway":      func(g *GatewayConfig, c *ConnectorConfig) { g.LocalTunnel = nil },
		"address mismatch":     func(g *GatewayConfig, c *ConnectorConfig) { c.LocalTunnel.Address = "127.0.0.1:18444" },
		"SNI mismatch":         func(g *GatewayConfig, c *ConnectorConfig) { c.LocalTunnel.ServerName = "other.example.test" },
		"Host mismatch":        func(g *GatewayConfig, c *ConnectorConfig) { c.LocalTunnel.HTTPHost = "other.example.test" },
		"public trust":         func(g *GatewayConfig, c *ConnectorConfig) { c.LocalTunnel.TrustFile = c.PublicTrustFile },
		"gateway public trust": func(g *GatewayConfig, c *ConnectorConfig) { g.LocalTunnel.TrustFile = c.PublicTrustFile },
		"leaf as root":         func(g *GatewayConfig, c *ConnectorConfig) { c.LocalTunnel.TrustFile = g.LocalTunnel.CertificateFile },
		"key as root":          func(g *GatewayConfig, c *ConnectorConfig) { c.LocalTunnel.TrustFile = g.LocalTunnel.PrivateKeyFile },
		"duplicate files": func(g *GatewayConfig, c *ConnectorConfig) {
			g.LocalTunnel.PrivateKeyFile = g.LocalTunnel.CertificateFile
		},
		"gateway state": func(g *GatewayConfig, c *ConnectorConfig) {
			g.LocalTunnel.TrustFile = g.StateDirectory + "/tunnel-roots.pem"
		},
		"connector state": func(g *GatewayConfig, c *ConnectorConfig) { c.LocalTunnel.TrustFile = c.StateDirectory + "/inner.pem" },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			g, c := localTunnelSettings(t)
			mutate(&g, &c)
			if err := c.ValidateGateway(g); !errors.Is(err, ErrConfiguration) {
				t.Fatal("invalid binding accepted", err)
			}
		})
	}
	g, c = localTunnelSettings(t)
	for _, host := range []string{g.ControlHost, g.TunnelHost, g.Gateway.Resources[0].Rule.Host, strings.TrimPrefix(g.OIDC.Issuer, "https://"), "admin.anvil-connect.internal", "gateway.anvil-connect.internal", "tunnel.anvil-connect.internal", "tunnel-gate.anvil-connect.internal", c.ID + ".connector.anvil-connect.internal"} {
		for _, field := range []string{"server_name", "http_host"} {
			g, c = localTunnelSettings(t)
			if field == "server_name" {
				g.LocalTunnel.ServerName, c.LocalTunnel.ServerName = host, host
			} else {
				g.LocalTunnel.HTTPHost, c.LocalTunnel.HTTPHost = host, host
			}
			if err := c.ValidateGateway(g); err == nil || !strings.Contains(err.Error(), "existing service identity") {
				t.Fatalf("collision %q: %v", host, err)
			}
		}
	}
	g, c = localTunnelSettings(t)
	for _, address := range []string{g.Gateway.Listen, g.TunnelListen, g.Gateway.Resources[0].TunnelAddress, c.Resources[0].ReverseAddress, c.Resources[0].Envelope.Listen, strings.TrimSuffix(strings.TrimPrefix(c.Resources[0].Envelope.OriginURL, "http://"), "/")} {
		g, c = localTunnelSettings(t)
		g.LocalTunnel.Listen, c.LocalTunnel.Address = address, address
		if err := c.ValidateGateway(g); err == nil || !strings.Contains(err.Error(), "existing listener") {
			t.Fatalf("collision %q: %v", address, err)
		}
	}
}

// Python supplies the same raw malformed corpus it rejects, including duplicate
// declarations. This tests the real closed readers without startup or PKI I/O.
func TestLocalTunnelReaderParity(t *testing.T) {
	path := os.Getenv("ANVIL_CONNECT_SCHEMA_CASES")
	if path == "" {
		t.Skip("cross-language corpus is supplied by tests/connect/test_render.py")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Mode    string
		Raw     string
		Gateway string
		Reason  string
		Valid   bool
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) == 0 {
		t.Fatal("empty parity corpus")
	}
	for index, c := range cases {
		var err error
		switch c.Mode {
		case "gateway":
			_, err = ReadGateway(strings.NewReader(c.Raw))
		case "connector":
			var connector ConnectorConfig
			connector, err = ReadConnector(strings.NewReader(c.Raw))
			if err == nil && c.Gateway != "" {
				var gateway GatewayConfig
				gateway, err = ReadGateway(strings.NewReader(c.Gateway))
				if err == nil {
					err = connector.ValidateGateway(gateway)
				}
			}
		default:
			t.Fatal("unknown corpus mode")
		}
		if c.Valid != (err == nil) || (err != nil && !strings.Contains(err.Error(), c.Reason)) {
			t.Fatalf("case %d (%s, valid=%t, reason=%q): %v", index, c.Mode, c.Valid, c.Reason, err)
		}
	}
}
