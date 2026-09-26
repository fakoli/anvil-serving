package runtime

import (
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"strings"
	"testing"
)

func TestRouterSecretsCannotReuseAnotherAuthorityValue(t *testing.T) {
	declaration := GatewayConfig{OIDC: OIDC{ClientSecretEnv: "OIDC"}, Gateway: config.Gateway{Resources: []config.Resource{{IdentityKeyEnv: "APP"}}}}
	values := map[string]string{"OIDC": "oidc-value", "APP": "app-value"}
	source := func(name string) (string, bool) { value, ok := values[name]; return value, ok }
	if distinctRouterSecrets(declaration, source, "signing-value", "checking-value") != nil {
		t.Fatal("independent credentials rejected")
	}
	for _, pair := range [][2]string{{"oidc-value", "check"}, {"sign", "oidc-value"}, {"app-value", "check"}, {"sign", "app-value"}} {
		if distinctRouterSecrets(declaration, source, pair[0], pair[1]) == nil {
			t.Fatal("credential substitution accepted")
		}
	}
}

func TestRouterSecretReferencesRemainDistinct(t *testing.T) {
	declaration := browserLifetimeGateway(t)
	declaration.Gateway.PortalHost = "home.example.test"
	declaration.Gateway.BrowserAdministration = &config.BrowserAdministration{BrowserResource: "dashboard", Operators: []string{"human:" + strings.Repeat("a", 64)}}
	resource := &declaration.Gateway.Resources[1]
	resource.Rule.NativeAuth, resource.IdentityKeyEnv, resource.IdentityKeyID = "signed-identity", "APP_IDENTITY", "app-key"
	declaration.Gateway.RouterKeys = &config.RouterKeys{URL: "https://router.example.test", SecretEnv: "ROUTER_SIGN", CheckEnv: "ROUTER_CHECK"}
	if err := declaration.Validate(); err != nil {
		t.Fatal("valid fixture", err)
	}
	for _, ref := range []string{declaration.OIDC.ClientSecretEnv, resource.IdentityKeyEnv} {
		for _, signing := range []bool{true, false} {
			keys := declaration.Gateway.RouterKeys
			if signing {
				keys.SecretEnv = ref
			} else {
				keys.CheckEnv = ref
			}
			if declaration.Validate() == nil {
				t.Fatal("authority reference reused")
			}
			keys.SecretEnv, keys.CheckEnv = "ROUTER_SIGN", "ROUTER_CHECK"
		}
	}
}
