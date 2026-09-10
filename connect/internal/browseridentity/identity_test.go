package browseridentity

import (
	"bytes"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

func testSigner(t *testing.T) *Signer {
	t.Helper()
	signer, err := NewSigner("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8", "observatory-v1")
	if err != nil {
		t.Fatal(err)
	}
	signer.now = func() time.Time { return time.Unix(1_789_000_000, 0).UTC() }
	signer.random = bytes.NewReader(bytes.Repeat([]byte{0x42}, 16))
	return signer
}

func testAdmission() session.Admission {
	return session.Admission{SessionID: strings.Repeat("1", 32), SessionGeneration: 3, Principal: "human:" + strings.Repeat("a", 64), PrincipalGeneration: 7, Resource: "dash", Host: "dash.example.test", Epoch: strings.Repeat("b", 64), ExpiresAt: time.Unix(1_789_000_100, 0).UTC()}
}

func testRule() config.Rule {
	return config.Rule{ID: "dash", Host: "dash.example.test", PathPrefix: "/", Methods: []string{"GET", "POST"}, Access: "browser", NativeAuth: "signed-identity", Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 30}}
}

func TestSignWireVector(t *testing.T) {
	signer := testSigner(t)
	request := httptest.NewRequest("GET", "https://dash.example.test/api/observatory/v1/session?view=current", nil)
	request.Host = "dash.example.test"
	assertion, err := signer.Sign(testAdmission(), testRule(), request)
	if err != nil || !ValidWire(assertion) {
		t.Fatal("signed assertion was not a valid wire value", err)
	}
	const want = "acai1.eyJ2IjoxLCJpc3MiOiJhbnZpbC1jb25uZWN0Iiwia2lkIjoib2JzZXJ2YXRvcnktdjEiLCJzdWIiOiJodW1hbjphYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhIiwic2lkIjoiMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTEiLCJzZyI6MywicGciOjcsImVwb2NoIjoiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYiIsInJlc291cmNlIjoiZGFzaCIsImhvc3QiOiJkYXNoLmV4YW1wbGUudGVzdCIsIm1ldGhvZCI6IkdFVCIsInRhcmdldF9zaGEyNTYiOiIyNTlmYzM1YjI0NGMwMmFkNzQ5YjFkMWI5MjNjZWRlNGRhNDc0MjI3MGMzZGQ3ZDgwYjk3MTY5MzhhODNkNTNjIiwiaWF0IjoxNzg5MDAwMDAwLCJleHAiOjE3ODkwMDAwMzAsInNlc3Npb25fZXhwIjoxNzg5MDAwMTAwLCJqdGkiOiI0MjQyNDI0MjQyNDI0MjQyNDI0MjQyNDI0MjQyNDI0MiJ9.R0uvUBMc8ZiV8yDMsvG0gkzhHzt1wCHIHK2iB6wOAMs"
	if assertion != want {
		t.Fatal("cross-language assertion vector changed")
	}
}

func TestSignerRejectsMalformedInputs(t *testing.T) {
	for _, secret := range []string{"", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHg"} {
		if signer, err := NewSigner(secret, "observatory-v1"); err == nil || signer != nil {
			t.Fatal("malformed identity secret accepted")
		}
	}
	if signer, err := NewSigner("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8", "UPPER"); err == nil || signer != nil {
		t.Fatal("invalid key id accepted")
	}
	for _, token := range []string{"", "acai1..", "acai1.e30.bad", "acai1.eyJ2IjoxfQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="} {
		if ValidWire(token) {
			t.Fatal("malformed assertion accepted")
		}
	}
}
