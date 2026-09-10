package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/ingresshttp"
)

func TestGatewayLegacyDeclarationIsReadableButCannotActivate(t *testing.T) {
	c := gatewaySettings(t)
	encoded, err := json.Marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := ReadGateway(bytes.NewReader(encoded))
	if err != nil || decoded.Ingress != nil {
		t.Fatal("legacy inspection declaration rejected", err)
	}
	called := false
	gateway, err := StartGateway(context.Background(), decoded, func(string) (string, bool) {
		called = true
		return "", false
	})
	if gateway != nil || err == nil || called {
		t.Fatal("legacy activation reached runtime composition")
	}
	if _, err := os.Lstat(c.StateDirectory); !os.IsNotExist(err) {
		t.Fatal("legacy activation touched authority state")
	}
}

func TestGatewayIngressDeclarationRejectsMalformedAndOverlappingPolicy(t *testing.T) {
	c := gatewaySettings(t)
	c.Ingress = &ingresshttp.Policy{GatewayUID: 1201, EdgeUID: 1202, GroupID: 1290, Directory: "/run/connect-fixture/ingress"}
	encoded, err := json.Marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ReadGateway(bytes.NewReader(encoded)); err != nil {
		t.Fatal("complete isolation declaration rejected", err)
	}
	for name, replacement := range map[string]string{
		"null":     `null`,
		"partial":  `{"gateway_uid":1201}`,
		"same-uid": `{"gateway_uid":1201,"edge_uid":1201,"group_id":1290,"directory":"/run/connect-fixture/ingress"}`,
		"boolean":  `{"gateway_uid":true,"edge_uid":1202,"group_id":1290,"directory":"/run/connect-fixture/ingress"}`,
		"unknown":  `{"gateway_uid":1201,"edge_uid":1202,"group_id":1290,"directory":"/run/connect-fixture/ingress","allow_legacy":true}`,
	} {
		t.Run(name, func(t *testing.T) {
			var raw map[string]json.RawMessage
			if err := json.Unmarshal(encoded, &raw); err != nil {
				t.Fatal(err)
			}
			raw["ingress"] = json.RawMessage(replacement)
			corrupt, err := json.Marshal(raw)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := ReadGateway(bytes.NewReader(corrupt)); err == nil {
				t.Fatal("malformed isolation declaration accepted")
			}
		})
	}
	for _, directory := range []string{c.StateDirectory, filepath.Dir(c.StateDirectory), filepath.Join(c.StateDirectory, "ingress")} {
		c.Ingress.Directory = directory
		if c.Validate() == nil {
			t.Fatal("ingress directory overlaps private authority")
		}
	}
}

func TestGatewayRejectsUnprovisionedIngressBeforeAuthorityState(t *testing.T) {
	c := gatewaySettings(t)
	c.Ingress = &ingresshttp.Policy{GatewayUID: 1201, EdgeUID: 1202, GroupID: 1290, Directory: filepath.Join(t.TempDir(), "missing")}
	called := false
	gateway, err := StartGateway(context.Background(), c, func(string) (string, bool) { called = true; return "", false })
	if err == nil || gateway != nil || called {
		t.Fatal("unprovisioned ingress reached authority composition")
	}
	if _, err := os.Lstat(c.StateDirectory); !os.IsNotExist(err) {
		t.Fatal("unprovisioned ingress touched authority state")
	}
	if gateway, err := ComposeGatewayForProtocolFixture(context.Background(), c, func(string) (string, bool) { return "", false }); err == nil || gateway != nil {
		t.Fatal("protocol fixture seam bypassed declared isolation")
	}
}
