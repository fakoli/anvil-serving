package httpedge

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func request() *http.Request {
	r := httptest.NewRequest("POST", "/v1/chat/completions?model=declared", strings.NewReader("{}"))
	r.Host = "api.example.test"
	return r
}

func TestHeadDeniesAmbiguity(t *testing.T) {
	if err := ValidateHead(request()); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(*http.Request){
		"host-port":           func(r *http.Request) { r.Host += ":443" },
		"host-case":           func(r *http.Request) { r.Host = "API.example.test" },
		"extra-host":          func(r *http.Request) { r.Header.Set("Host", r.Host) },
		"absolute-target":     func(r *http.Request) { r.URL.Scheme = "https"; r.URL.Host = r.Host },
		"encoded-path":        func(r *http.Request) { r.URL.RawPath = "/v1/%63hat/completions" },
		"dot-path":            func(r *http.Request) { r.URL.Path = "/v1/../admin" },
		"duplicate-separator": func(r *http.Request) { r.URL.Path = "//" },
		"two-lengths":         func(r *http.Request) { r.Header["Content-Length"] = []string{"2", "2"} },
		"length-mismatch":     func(r *http.Request) { r.Header.Set("Content-Length", "3") },
		"length-plus-chunks":  func(r *http.Request) { r.TransferEncoding = []string{"chunked"} },
		"raw-framing":         func(r *http.Request) { r.Header.Set("Transfer-Encoding", "chunked") },
		"trailer":             func(r *http.Request) { r.Trailer = http.Header{"Authorization": []string{"later"}} },
		"query-key":           func(r *http.Request) { r.URL.RawQuery = "api_key=untrusted" },
		"malformed-query":     func(r *http.Request) { r.URL.RawQuery = "a=%" },
		"header-injection":    func(r *http.Request) { r.Header.Set("X-Test", "one\r\nAuthorization: two") },
		"invalid-name":        func(r *http.Request) { r.Header["X-<Test>"] = []string{"bad"} },
		"hop-credential":      func(r *http.Request) { r.Header.Set("Connection", "X-Anvil-Connect-Assertion") },
		"unsupported-upgrade": func(r *http.Request) { r.Header.Set("Connection", "upgrade"); r.Header.Set("Upgrade", "h2c") },
		"header-budget":       func(r *http.Request) { r.Header.Set("X-Test", strings.Repeat("x", 65537)) },
	} {
		t.Run(name, func(t *testing.T) {
			r := request()
			mutate(r)
			if ValidateHead(r) == nil {
				t.Fatal("ambiguous head accepted")
			}
		})
	}
	chunked := request()
	chunked.ContentLength = -1
	chunked.TransferEncoding = []string{"chunked"}
	if err := ValidateHead(chunked); err == nil {
		t.Fatal("HTTP/1 chunked upload admitted despite erased TE+CL ambiguity")
	}
}

func TestCredentialCarriers(t *testing.T) {
	for _, carrier := range []string{"Authorization", "X-Api-Key"} {
		r := request()
		value := "example-key"
		if carrier == "Authorization" {
			value = "Bearer " + value
		}
		r.Header.Set(carrier, value)
		if got, err := APIKey(r); err != nil || got != "example-key" {
			t.Fatal("SDK carrier failed")
		}
	}
	for _, headers := range []http.Header{
		{}, {"Authorization": {"Bearer one", "Bearer two"}},
		{"Authorization": {"Bearer one"}, "authorization": {"Bearer two"}},
		{"Authorization": {"Bearer one"}, "X-Api-Key": {"one"}},
		{"Authorization": {"Bearer one, Bearer two"}},
		{"Authorization": {"Basic one"}},
		{"Authorization": {"Bearer one"}, "Origin": {"https://api.example.test"}},
		{"Authorization": {"Bearer one"}, "Cookie": {"session=ambiguous"}},
		{"Authorization": {"Bearer one"}, "Proxy-Authorization": {"Basic proxy"}},
	} {
		r := request()
		r.Header = headers
		if _, err := APIKey(r); err == nil {
			t.Fatal("ambiguous credentials accepted")
		}
	}
}

func TestCredentialAndIdentityStripping(t *testing.T) {
	trusted := http.Header{"Content-Type": {"application/json"}, "Anthropic-Version": {"2023-06-01"}, "X-Request-Id": {"client-correlation"}}
	header := trusted.Clone()
	for _, name := range []string{"Authorization", "X-Api-Key", "Proxy-Authorization", "Cookie", "Forwarded", "X-Forwarded-Host", "X-Forwarded-For", "X-Real-IP", "Remote-User", "Remote-Email", "X-Auth-Request-User", "X-Authenticated-User", "X-User-Id", "X-Anvil-Connect-Assertion", "Tailscale-User-Login", "Cf-Access-Authenticated-User-Email", "X-Goog-Authenticated-User-Email", "X-Amzn-Oidc-Identity", "X-Original-URL", "X-Rewrite-URL", "X-Unknown-Trusted-Identity"} {
		header.Set(name, "spoofed")
	}
	CleanAPIHeaders(header)
	if len(header) != len(trusted) {
		t.Fatal("credential or identity header survived")
	}
	for name, value := range trusted {
		if header.Get(name) != value[0] {
			t.Fatal("application protocol header removed")
		}
	}
}
