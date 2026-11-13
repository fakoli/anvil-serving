package config

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func TestOperatorResourcePolicyIsOptionalAndClosed(t *testing.T) {
	declaration := deviceGateway(t, "device")
	declaration.BrowserAdministration = &BrowserAdministration{BrowserResource: "dash", Operators: []string{"human:" + strings.Repeat("a", 64)}}
	for _, restricted := range []bool{false, true} {
		declaration.Resources[1].OperatorOnly = &restricted
		data, err := json.Marshal(declaration)
		if err != nil {
			t.Fatal(err)
		}
		decoded, err := ReadGateway(bytes.NewReader(data))
		if err != nil || decoded.Resources[1].RequiresOperator() != restricted {
			t.Fatalf("browser policy did not round trip: %v", err)
		}
	}
	data, _ := json.Marshal(declaration)
	for _, invalid := range []string{"null", `"true"`, "1", "[]", "{}"} {
		bad := bytes.Replace(data, []byte(`"operator_only":true`), []byte(`"operator_only":`+invalid), 1)
		if _, err := ReadGateway(bytes.NewReader(bad)); err == nil {
			t.Fatalf("malformed operator policy accepted: %s", invalid)
		}
	}
	declaration.BrowserAdministration = nil
	if declaration.Validate() == nil {
		t.Fatal("operator restriction accepted without its authority")
	}
	declaration.Resources[1].OperatorOnly = nil
	if declaration.Validate() != nil {
		t.Fatal("omitted policy changed existing behavior")
	}
	for _, restricted := range []bool{false, true} {
		declaration.Resources[0].OperatorOnly = &restricted
		if declaration.Validate() == nil {
			t.Fatal("browser policy accepted on API resource")
		}
	}
}
