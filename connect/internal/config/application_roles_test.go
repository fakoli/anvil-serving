package config

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestExternalRoleMetadataIsBrowserPassthroughOnly(t *testing.T) {
	for _, tc := range []struct {
		name, access, native string
		metadata             any
		valid                bool
	}{
		{"external", "browser", "passthrough", "external", true},
		{"unknown", "browser", "passthrough", "native", false},
		{"empty", "browser", "passthrough", "", false},
		{"null", "browser", "passthrough", nil, false},
		{"boolean", "browser", "passthrough", true, false},
		{"api", "api", "delegate-bearer", "external", false},
		{"unauthenticated", "browser", "none", "external", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := gateway(t)
			g.Resources[0].Rule.Access, g.Resources[0].Rule.NativeAuth = tc.access, tc.native
			raw, _ := json.Marshal(g)
			var payload map[string]any
			if err := json.Unmarshal(raw, &payload); err != nil {
				t.Fatal(err)
			}
			payload["resources"].([]any)[0].(map[string]any)["role_management"] = tc.metadata
			raw, _ = json.Marshal(payload)
			decoded, err := ReadGateway(bytes.NewReader(raw))
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v error=%v", tc.valid, err)
			}
			if tc.valid {
				before, _ := json.Marshal(g.Resources[0].Rule)
				after, _ := json.Marshal(decoded.Resources[0].Rule)
				if !bytes.Equal(before, after) {
					t.Fatal("role metadata changed routing authority")
				}
			}
		})
	}
}
