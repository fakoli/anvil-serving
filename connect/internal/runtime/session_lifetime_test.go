package runtime

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

func browserLifetimeGateway(t *testing.T) GatewayConfig {
	t.Helper()
	declaration := gatewaySettings(t)
	declaration.Gateway.Resources = append(declaration.Gateway.Resources, config.Resource{
		Rule: config.Rule{
			ID: "dashboard", Host: "dash.example.test", PathPrefix: "/",
			Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "none",
			Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 60, DurationSeconds: 120},
		},
		Connector: "origin-b", TunnelAddress: availableAddress(t),
	})
	declaration.OIDC = OIDC{Issuer: "https://auth.example.test", ClientID: "connect-browser", ClientSecretEnv: "OIDC_CLIENT_SECRET"}
	return declaration
}

func TestBrowserSessionLifetimeDeclarationDefaultsAndBounds(t *testing.T) {
	omitted := browserLifetimeGateway(t)
	encoded, err := json.Marshal(omitted)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encoded, []byte(`browser_session_lifetime_seconds`)) {
		t.Fatal("omitted lifetime serialized into the legacy declaration")
	}
	decoded, err := ReadGateway(bytes.NewReader(encoded))
	if err != nil || decoded.BrowserSessionLifetimeSeconds != nil || decoded.BrowserSessionLifetime() != session.DefaultSessionLifetime {
		t.Fatal("omitted browser lifetime did not retain the established default", err)
	}

	for _, seconds := range []int{60, 86400} {
		candidate := browserLifetimeGateway(t)
		candidate.BrowserSessionLifetimeSeconds = &seconds
		encoded, err := json.Marshal(candidate)
		if err != nil {
			t.Fatal(err)
		}
		decoded, err := ReadGateway(bytes.NewReader(encoded))
		if err != nil || decoded.BrowserSessionLifetimeSeconds == nil || *decoded.BrowserSessionLifetimeSeconds != seconds || decoded.BrowserSessionLifetime() != time.Duration(seconds)*time.Second {
			t.Fatalf("valid browser lifetime %d was not retained", seconds)
		}
	}
}

func TestBrowserSessionLifetimeDeclarationRejectsMalformedValues(t *testing.T) {
	seconds := 60
	declaration := browserLifetimeGateway(t)
	declaration.BrowserSessionLifetimeSeconds = &seconds
	encoded, err := json.Marshal(declaration)
	if err != nil {
		t.Fatal(err)
	}
	needle := []byte(`"browser_session_lifetime_seconds":60`)
	for name, replacement := range map[string]string{
		"null":      "null",
		"zero":      "0",
		"negative":  "-1",
		"below-min": "59",
		"above-max": "86401",
		"boolean":   "true",
		"string":    `"60"`,
	} {
		t.Run(name, func(t *testing.T) {
			corrupt := bytes.Replace(encoded, needle, []byte(`"browser_session_lifetime_seconds":`+replacement), 1)
			if bytes.Equal(corrupt, encoded) || strings.Contains(string(corrupt), "fixture-secret") {
				t.Fatal("invalid native lifetime test setup")
			}
			if _, err := ReadGateway(bytes.NewReader(corrupt)); err == nil {
				t.Fatal("malformed browser lifetime accepted")
			}
		})
	}

	apiOnly := gatewaySettings(t)
	apiOnly.BrowserSessionLifetimeSeconds = &seconds
	if apiOnly.Validate() == nil {
		t.Fatal("API-only gateway accepted a browser session lifetime")
	}
}
