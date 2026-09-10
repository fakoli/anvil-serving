package config

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func example(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile("../../examples/" + name + ".json")
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func gateway(t *testing.T) Gateway {
	t.Helper()
	g, err := ReadGateway(bytes.NewReader(example(t, "connect")))
	if err != nil {
		t.Fatal(err)
	}
	return g
}

func connector(t *testing.T) Connector {
	t.Helper()
	c, err := ReadConnector(bytes.NewReader(example(t, "connector")))
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestClosedJSON(t *testing.T) {
	valid := string(example(t, "connect"))
	for name, invalid := range map[string]string{
		"unknown":          strings.Replace(valid, `"schema":`, `"token":"must-not-appear-in-errors", "schema":`, 1),
		"duplicate":        strings.Replace(valid, `"schema":`, `"listen":"127.0.0.1:1", "schema":`, 1),
		"case-alias":       strings.Replace(valid, `"host":`, `"Host":`, 1),
		"nested-duplicate": strings.Replace(valid, `"methods":`, `"id":"different", "methods":`, 1),
		"null":             strings.Replace(valid, `"GET"`, `null`, 1),
		"trailing":         valid + `{}`,
		"number":           strings.Replace(valid, `"concurrent": 16`, `"concurrent": "16"`, 1),
		"overflow":         strings.Replace(valid, `"concurrent": 16`, `"concurrent": 1000000000000000000000000000`, 1),
		"too-large":        strings.Repeat(" ", MaxConfigBytes) + valid,
	} {
		t.Run(name, func(t *testing.T) {
			_, err := ReadGateway(strings.NewReader(invalid))
			if err == nil {
				t.Fatal("invalid configuration accepted")
			}
			if strings.Contains(err.Error(), "must-not-appear-in-errors") {
				t.Fatal("configuration value leaked")
			}
		})
	}
}

func TestGatewayDenials(t *testing.T) {
	for name, mutate := range map[string]func(*Gateway){
		"public-bind":      func(g *Gateway) { g.Listen = "0.0.0.0:9080" },
		"implicit-port":    func(g *Gateway) { g.Listen = "127.0.0.1:0" },
		"missing-schema":   func(g *Gateway) { g.Schema = "" },
		"unbounded-global": func(g *Gateway) { g.MaxConcurrent = 0 },
		"unbounded-body":   func(g *Gateway) { g.Resources[0].Rule.Limits.RequestBytes = 0 },
		"buffer-budget":    func(g *Gateway) { g.Resources[0].Rule.Limits.BufferBytes = 262145 },
		"unsafe-timeout":   func(g *Gateway) { g.Resources[0].Rule.Limits.DurationSeconds = 1 },
		"global-smaller":   func(g *Gateway) { g.MaxConcurrent = 1 },
		"duplicate-id": func(g *Gateway) {
			r := g.Resources[0]
			r.Rule.Host = "second.example.test"
			r.TunnelAddress = "127.0.0.1:9083"
			g.Resources = append(g.Resources, r)
		},
		"duplicate-host": func(g *Gateway) {
			r := g.Resources[0]
			r.Rule.ID = "second"
			r.TunnelAddress = "127.0.0.1:9083"
			g.Resources = append(g.Resources, r)
		},
		"duplicate-listener": func(g *Gateway) { g.Resources[0].TunnelAddress = g.Listen },
		"remote-tunnel":      func(g *Gateway) { g.Resources[0].TunnelAddress = "100.64.0.10:9000" },
		"dot-path":           func(g *Gateway) { g.Resources[0].Rule.PathPrefix = "/v1/../admin" },
		"encoded-path":       func(g *Gateway) { g.Resources[0].Rule.PathPrefix = "/v1%2fadmin" },
		"trailing-path":      func(g *Gateway) { g.Resources[0].Rule.PathPrefix = "/v1/" },
		"wildcard-host":      func(g *Gateway) { g.Resources[0].Rule.Host = "*.example.test" },
		"case-host":          func(g *Gateway) { g.Resources[0].Rule.Host = "API.example.test" },
		"trailing-host":      func(g *Gateway) { g.Resources[0].Rule.Host = "api.example.test." },
		"host-port":          func(g *Gateway) { g.Resources[0].Rule.Host = "api.example.test:443" },
		"tunnel-method":      func(g *Gateway) { g.Resources[0].Rule.Methods = []string{"CONNECT"} },
		"duplicate-method":   func(g *Gateway) { g.Resources[0].Rule.Methods = []string{"GET", "GET"} },
		"no-native-api":      func(g *Gateway) { g.Resources[0].Rule.NativeAuth = "none" },
	} {
		t.Run(name, func(t *testing.T) {
			g := gateway(t)
			mutate(&g)
			data, err := json.Marshal(g)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := ReadGateway(bytes.NewReader(data)); err == nil {
				t.Fatal("unsafe gateway declaration accepted")
			}
		})
	}
}

func TestIndependentConnectorEnvelope(t *testing.T) {
	local := connector(t)
	remote := gateway(t)
	remote.Resources[0].Rule.PathPrefix = "/"
	remote.Resources[0].Rule.Methods = []string{"GET", "POST", "DELETE"}
	if !remote.Resources[0].Rule.Allows("api.example.test", "/admin", "DELETE") {
		t.Fatal("invalid test setup")
	}
	if local.Resources[0].Rule.Allows("api.example.test", "/admin", "DELETE") {
		t.Fatal("gateway widened local envelope")
	}
	for _, target := range []string{
		"http://100.64.0.10:19000", "http://127.0.0.1:19000/admin", "http://user:password@127.0.0.1:19000",
		"http://127.0.0.1:19000?x=1", "http://127.0.0.1:19000#fragment", "file:///etc/passwd", "http://127.0.0.1:019000", "http://127.0.0.1",
		"http://127.0.0.1:19000#", "http://127.0.0.1:19000/#",
	} {
		t.Run(target, func(t *testing.T) {
			c := connector(t)
			c.Resources[0].OriginURL = target
			if c.Validate() == nil {
				t.Fatal("unsafe origin accepted")
			}
		})
	}
	for _, secret := range []string{"", "env:MISSING", "literal-value", "${TOKEN}", "/path/to/key"} {
		c := connector(t)
		c.Resources[0].TokenEnv = secret
		if c.Validate() == nil {
			t.Fatal("missing or literal credential accepted")
		}
	}
	cycle := connector(t)
	cycle.Resources[0].OriginURL = "http://" + cycle.Resources[0].Listen
	if cycle.Validate() == nil {
		t.Fatal("connector can target itself")
	}
	data := strings.Replace(string(example(t, "connector")), `"token_env":`, `"token":"must-not-appear-in-errors", "token_env":`, 1)
	if _, err := ReadConnector(strings.NewReader(data)); err == nil || strings.Contains(err.Error(), "must-not-appear-in-errors") {
		t.Fatal("literal secret field not safely rejected")
	}
}

func TestResourceMatching(t *testing.T) {
	rule := gateway(t).Resources[0].Rule
	root := rule
	root.PathPrefix = "/"
	if root.Allows(root.Host, "//", "GET") {
		t.Fatal("duplicate root separator accepted")
	}
	for _, tc := range []struct {
		host, path, method string
		allowed            bool
	}{
		{"api.example.test", "/v1", "GET", true},
		{"api.example.test", "/v1/", "GET", true},
		{"api.example.test", "/v1/chat/completions", "POST", true},
		{"api.example.test", "/v10", "GET", false},
		{"other.example.test", "/v1", "GET", false},
		{"api.example.test", "/v1", "DELETE", false},
		{"api.example.test", "/v1/../admin", "GET", false},
		{"api.example.test", "/v1//admin", "GET", false},
		{"api.example.test", "/v1/%2e%2e/admin", "GET", false},
		{"api.example.test", "/v1\\admin", "GET", false},
		{"api.example.test", "/v1?admin", "GET", false},
	} {
		if got := rule.Allows(tc.host, tc.path, tc.method); got != tc.allowed {
			t.Errorf("%+v: got %v", tc, got)
		}
	}
}

func TestSignedIdentityGatewayBinding(t *testing.T) {
	valid := gateway(t)
	resource := &valid.Resources[0]
	resource.Rule.Access = "browser"
	resource.Rule.NativeAuth = "signed-identity"
	resource.IdentityKeyEnv = "ANVIL_CONNECT_DASH_IDENTITY_KEY"
	resource.IdentityKeyID = "dash-v1"
	if valid.Validate() != nil {
		t.Fatal("valid signed identity resource rejected")
	}
	for name, mutate := range map[string]func(*Gateway){
		"missing-env":    func(g *Gateway) { g.Resources[0].IdentityKeyEnv = "" },
		"invalid-env":    func(g *Gateway) { g.Resources[0].IdentityKeyEnv = "literal" },
		"missing-key-id": func(g *Gateway) { g.Resources[0].IdentityKeyID = "" },
		"api-profile": func(g *Gateway) {
			g.Resources[0].Rule.Access, g.Resources[0].Rule.NativeAuth = "api", "delegate-bearer"
		},
		"ordinary-profile": func(g *Gateway) { g.Resources[0].Rule.NativeAuth = "none" },
	} {
		t.Run(name, func(t *testing.T) {
			candidate := valid
			candidate.Resources = append([]Resource(nil), valid.Resources...)
			mutate(&candidate)
			if candidate.Validate() == nil {
				t.Fatal("invalid identity binding accepted")
			}
		})
	}
	second := valid.Resources[0]
	second.Rule.ID, second.Rule.Host, second.TunnelAddress = "dash-two", "dash-two.example.test", "127.0.0.1:19002"
	valid.Resources = append(valid.Resources, second)
	if valid.Validate() == nil {
		t.Fatal("identity key was reused across resources")
	}
}
