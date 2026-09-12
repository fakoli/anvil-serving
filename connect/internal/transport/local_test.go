package transport

import (
	"context"
	"crypto/rand"
	"crypto/x509"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

func localOptions(t *testing.T) ClientOptions {
	t.Helper()
	dir := t.TempDir()
	empty := filepath.Join(dir, "empty")
	headers := filepath.Join(dir, "headers")
	trust := filepath.Join(dir, "trust.pem")
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(empty, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(headers, []byte("Authorization: Bearer synthetic\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(trust, testpki.New(t).PEM(), 0600); err != nil {
		t.Fatal(err)
	}
	return ClientOptions{ViaGate: true, ServerURL: "wss://127.0.0.1:18443", ReverseAddress: "127.0.0.1:9081", OriginAddress: "127.0.0.1:9181", TrustFile: trust, EmptyTrustDirectory: empty, HeadersFile: headers, Local: &LocalBinding{Address: "127.0.0.1:18443", ServerName: "local-tls.example.test", Host: "local-http.example.test"}}
}
func TestLocalPinAdapterExactArgumentsAndEnvironment(t *testing.T) {
	for _, name := range []string{"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "WSTUNNEL_HTTP_UPGRADE_PATH_PREFIX", "WSTUNNEL_HTTP_PROXY_PASSWORD"} {
		t.Setenv(name, "untrusted-inherited-value")
	}
	o := localOptions(t)
	var in inputs
	defer in.close()
	env, args, err := clientCommand(o, &in)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"client", "--no-color", "--log-lvl", "warn", "--nb-worker-threads", "1", "--tls-verify-certificate", "--connection-retry-max-backoff", "1s", "--reverse-tunnel-connection-retry-max-backoff", "1s", "-R", "tcp://127.0.0.1:9081:127.0.0.1:9181", "--http-upgrade-path-prefix", "acv1", "--http-headers-file", "/proc/self/fd/6/headers", "--tls-sni-override", "local-tls.example.test", "--http-headers", "Host: local-http.example.test", "wss://127.0.0.1:18443"}
	if !reflect.DeepEqual(args, want) {
		t.Fatalf("local pin arguments:\n%q\nwant:\n%q", args, want)
	}
	if !reflect.DeepEqual(env, []string{"SSL_CERT_FILE=/proc/self/fd/4", "SSL_CERT_DIR=/proc/self/fd/5"}) {
		t.Fatal("trust or proxy environment escaped", env)
	}
	if strings.Contains(strings.Join(args, " "), "synthetic") {
		t.Fatal("authorization on argv")
	}
	// Existing public proxy and URL remain intact when selection is omitted.
	o.Local = nil
	o.ServerURL = "wss://tunnel.example.test"
	o.ProxyURL = "http://127.0.0.1:8080"
	var public inputs
	defer public.close()
	_, args, err = clientCommand(o, &public)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.Join(args, " "), "--tls-sni-override") || !reflect.DeepEqual(args[len(args)-3:], []string{"--http-proxy", o.ProxyURL, o.ServerURL}) {
		t.Fatal("public options changed", args)
	}
}
func TestLocalPinAdapterRejectsAmbiguity(t *testing.T) {
	for _, mode := range []string{"proxy", "dns", "ipv6", "wildcard", "zero", "url", "bad-name", "host-port", "public-url", "direct", "nonempty-trust"} {
		t.Run(mode, func(t *testing.T) {
			o := localOptions(t)
			switch mode {
			case "proxy":
				o.ProxyURL = "http://127.0.0.1:8080"
			case "dns":
				o.Local.Address = "local.example.test:18443"
			case "ipv6":
				o.Local.Address = "[::1]:18443"
			case "wildcard":
				o.Local.Address = "0.0.0.0:18443"
			case "zero":
				o.Local.Address = "127.0.0.1:0"
			case "url":
				o.Local.Address = "https://127.0.0.1:18443"
			case "bad-name":
				o.Local.ServerName = "*.example.test"
			case "host-port":
				o.Local.Host = "local.example.test:18443"
			case "public-url":
				o.ServerURL = "wss://tunnel.example.test"
			case "direct":
				o.ViaGate = false
			case "nonempty-trust":
				if err := os.WriteFile(filepath.Join(o.EmptyTrustDirectory, "root.pem"), []byte("foreign"), 0600); err != nil {
					t.Fatal(err)
				}
			}
			var in inputs
			defer in.close()
			if _, _, err := clientCommand(o, &in); err == nil {
				t.Fatal("unsafe local options accepted")
			}
		})
	}
}
func TestLocalTLSVerifierRejectsInvalidBindingBeforeDial(t *testing.T) {
	if VerifyLocalEntry(nil, LocalBinding{}, nil) == nil {
		t.Fatal("missing binding accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	o := localOptions(t)
	began := time.Now()
	if VerifyLocalEntry(ctx, *o.Local, testpki.New(t).Roots) == nil || time.Since(began) > time.Second {
		t.Fatal("cancelled local dial not bounded")
	}
}

type entryPeerAuthority struct{}

func (entryPeerAuthority) VerifyPeer(string, *x509.Certificate) (identity.Installation, error) {
	return identity.Installation{}, nil
}
func TestLocalEntryDoesNotDoubleDispatcherBudgetsAndDERRetirement(t *testing.T) {
	ca := testpki.New(t)
	g := declaration(t)
	d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true), entryPeerAuthority{})
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	for _, b := range d.resources {
		if b.transport.MaxConnsPerHost != 2 || b.upgradeTransport == b.transport || b.upgradeTransport.MaxConnsPerHost != b.resource.Rule.Limits.Concurrent || b.transport.Proxy != nil || b.upgradeTransport.Proxy != nil {
			t.Fatal("dispatcher transport contracts changed")
		}
	}
	for n := 0; n < g.MaxConcurrent; n++ {
		_, release, err := d.active.Watch(context.Background(), func() error { return nil })
		if err != nil {
			t.Fatal(err)
		}
		defer release()
	}
	if _, release, err := d.active.Watch(context.Background(), func() error { return nil }); err == nil {
		release()
		t.Fatal("application cap doubled")
	}
	registry := newConnRegistry()
	defer registry.close()
	leafTLS := ca.Leaf(t, ConnectorPeer("origin-a"), false)
	leaf, _ := x509.ParseCertificate(leafTLS.Certificate[0])
	other := *leaf
	other.SerialNumber = big.NewInt(999)
	root, key := ca.SigningIdentity(t)
	der, err := x509.CreateCertificate(rand.Reader, &other, root, leaf.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	otherLeaf, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	// Same key and names; only DER distinguishes the retirement binding.
	var peers []net.Conn
	for _, cert := range []*x509.Certificate{leaf, otherLeaf} {
		a, b := net.Pipe()
		defer b.Close()
		peers = append(peers, b)
		c := &trackedConn{Conn: a, registry: registry}
		registry.add(c)
		registry.bind(c, cert)
	}
	registry.retireLeaf(leaf.Raw)
	peers[0].SetReadDeadline(time.Now().Add(time.Second))
	if _, err := peers[0].Read(make([]byte, 1)); err == nil {
		t.Fatal("target DER survived")
	}
	registry.mu.Lock()
	remaining := len(registry.conns)
	registry.mu.Unlock()
	if remaining != 1 {
		t.Fatal("same-key sibling DER retired")
	}
}

type stalledRoundTrip struct{}

func (stalledRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) {
	<-r.Context().Done()
	return nil, r.Context().Err()
}

// This proves the pool/dial/TLS acquisition budget only. It does not prove a
// five-second bound after GotConn or an origin execution/readiness signal.
func TestLocalInnerConnectionAcquisitionHasTotalDeadline(t *testing.T) {
	ca := testpki.New(t)
	g := declaration(t)
	d, err := NewDispatcher(g, ca.Roots, ca.Leaf(t, GatewayPeer, true), entryPeerAuthority{})
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	resource := g.Resources[0]
	binding := d.resources[resource.Rule.ID]
	binding.proxy.Transport = stalledRoundTrip{}
	r := httptest.NewRequest("GET", "https://"+resource.Rule.Host+"/v1/models", nil)
	r.RequestURI, r.URL.Scheme, r.URL.Host = "/v1/models", "", ""
	began := time.Now()
	w := httptest.NewRecorder()
	d.dispatch(w, r, resource)
	if w.Code != 502 || time.Since(began) > 5500*time.Millisecond || time.Since(began) < 4*time.Second {
		t.Fatal("inner connection acquisition not bounded", w.Code, time.Since(began))
	}
}

func TestLocalRotatingHeadersCannotOverrideEntryHost(t *testing.T) {
	for _, data := range []string{"Authorization: Bearer synthetic\nHost: other.example.test\n", "Host: local-http.example.test\nAuthorization: Bearer synthetic\n", "Authorization: Bearer synthetic\r\n", "Authorization: Bearer \n", "Authorization: Bearer with space\n"} {
		o := localOptions(t)
		if err := os.WriteFile(o.HeadersFile, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		var in inputs
		_, _, err := clientCommand(o, &in)
		in.close()
		if err == nil {
			t.Fatal("non-authorization-only local headers accepted")
		}
	}
}
