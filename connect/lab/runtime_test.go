package lab

// This is an owned-process smoke behind a synthetic TLS/CONNECT front. It is
// intentionally not a Caddy qualification: it only proves the protocol-only
// same-UID fixture contracts over loopback, pinned wstunnel, and an opaque
// CONNECT bridge. Managed activation uses the declared cross-UID ingress.

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	connectruntime "github.com/fakoli/anvil-serving/connect/internal/runtime"
)

func labAddress(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().String()
}
func frontCertificate(t *testing.T, ca authority, names []string) tls.Certificate {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		t.Fatal(err)
	}
	tpl := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: names[0]}, DNSNames: names, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der := certificate(t, tpl, ca.cert, &key.PublicKey, ca.key)
	kd, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	pair, err := tls.X509KeyPair(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: kd}))
	if err != nil {
		t.Fatal(err)
	}
	return pair
}

func TestRuntimeOwnedProcessAPISmoke(t *testing.T) {
	if os.Getenv("ANVIL_CONNECT_WSTUNNEL") == "" {
		t.Skip("requires explicit pinned transport artifact")
	}
	binary := pinnedBinary(t)
	root := t.TempDir()
	ca := newAuthority(t)
	trust := filepath.Join(root, "public.pem")
	if err := os.WriteFile(trust, ca.pem, 0644); err != nil {
		t.Fatal(err)
	}
	apiHost, controlHost, tunnelHost := "api.example.test", "control.example.test", "tunnel.example.test"
	gatewayCfg := connectruntime.GatewayConfig{Schema: "anvil-connect.gateway-runtime/v1", Gateway: config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: labAddress(t), MaxConcurrent: 4, Resources: []config.Resource{{Rule: config.Rule{ID: "router", Host: apiHost, PathPrefix: "/", Methods: []string{"GET"}, Access: "api", NativeAuth: "delegate-bearer", Limits: config.Limits{RequestBytes: 4096, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 2, DurationSeconds: 10}}, Connector: "connector-a", TunnelAddress: labAddress(t)}}}, ControlHost: controlHost, TunnelHost: tunnelHost, StateDirectory: filepath.Join(root, "gateway"), TunnelBinary: binary, TunnelListen: labAddress(t)}
	if err := connectruntime.InitializeGateway(gatewayCfg); err != nil {
		t.Fatal(err)
	}
	gateway, err := connectruntime.ComposeGatewayForProtocolFixture(context.Background(), gatewayCfg, func(string) (string, bool) { return "", false })
	if err != nil {
		t.Fatal(err)
	}
	defer gateway.Close()
	gd, err := privatefiles.Open(gatewayCfg.StateDirectory)
	if err != nil {
		t.Fatal(err)
	}
	defer gd.Close()
	adminPin, err := gd.PinPath("admin.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer adminPin.Close()
	ingressPin, err := gd.PinPath("ingress.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer ingressPin.Close()
	h2 := new(http.Protocols)
	h2.SetUnencryptedHTTP2(true)
	ordinary := &http.Transport{Protocols: h2, DisableKeepAlives: true, DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", ingressPin.Path())
	}}
	defer ordinary.CloseIdleConnections()
	h1 := &http.Transport{DisableKeepAlives: true, DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", ingressPin.Path())
	}}
	defer h1.CloseIdleConnections()
	front := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tr := ordinary
		if strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
			tr = h1
		}
		p := &httputil.ReverseProxy{Transport: tr, Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL.Scheme = "http"
			pr.Out.URL.Host = "ingress"
			pr.Out.Host = pr.In.Host
		}, ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) { http.Error(w, "front", 502) }}
		p.ServeHTTP(w, r)
	}))
	front.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{frontCertificate(t, ca, []string{apiHost, controlHost, tunnelHost})}}
	front.EnableHTTP2 = true
	front.StartTLS()
	defer front.Close()
	proxy := httptest.NewServer(connectProxy(t, front.Listener.Addr().String(), map[string]bool{apiHost + ":443": true, controlHost + ":443": true, tunnelHost + ":443": true}))
	defer proxy.Close()
	pu, _ := url.Parse(proxy.URL)
	connectorCfg := connectruntime.ConnectorConfig{Schema: "anvil-connect.connector-runtime/v1", ID: "connector-a", ControlHost: controlHost, TunnelHost: tunnelHost, StateDirectory: filepath.Join(root, "connector"), TunnelBinary: binary, PublicTrustFile: trust, HTTPProxyURL: pu.String(), Resources: []connectruntime.ConnectorResource{{Envelope: config.Envelope{Rule: gatewayCfg.Gateway.Resources[0].Rule, Listen: labAddress(t), OriginURL: "http://127.0.0.1:" + strings.Split(labAddress(t), ":")[1], TokenEnv: "NATIVE"}, ReverseAddress: gatewayCfg.Gateway.Resources[0].TunnelAddress}}}
	// A fixed local native app replaces the placeholder origin selected above.
	nativeAddr := labAddress(t)
	connectorCfg.Resources[0].Envelope.OriginURL = "http://" + nativeAddr
	var calls atomic.Int32
	native, err := net.Listen("tcp4", nativeAddr)
	if err != nil {
		t.Fatal(err)
	}
	app := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("Authorization") != "Bearer synthetic-native" || r.Header.Get("X-Api-Key") != "" {
			http.Error(w, "auth", 401)
			return
		}
		io.WriteString(w, "runtime-ok")
	})}
	go app.Serve(native)
	defer app.Close()
	invite, err := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "invite", Installation: "connector-a", Role: "connector", Resources: []string{"router"}, LifetimeSeconds: 60})
	if err != nil {
		t.Fatal(err)
	}
	if err := connectruntime.InitializeConnector(context.Background(), connectorCfg, invite); err != nil {
		t.Fatal(err)
	}
	identity, err := connectruntime.ConnectorIdentity(connectorCfg)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "approve", Installation: "connector-a", Fingerprint: identity.Fingerprint}); err != nil {
		t.Fatal(err)
	}
	connector, err := connectruntime.StartConnector(context.Background(), connectorCfg, func(name string) (string, bool) { return "synthetic-native", name == "NATIVE" })
	if err != nil {
		t.Fatal(err)
	}
	defer connector.Close()
	grants := []access.Grant{{Resource: "router", Methods: []string{"GET"}}}
	if _, err := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "principal-set", Principal: "sdk", Grants: grants}); err != nil {
		t.Fatal(err)
	}
	issued, err := admin.Call(context.Background(), adminPin.Path(), admin.Request{Operation: "api-key-issue", Principal: "sdk", Grants: grants, LifetimeSeconds: 60})
	if err != nil {
		t.Fatal(err)
	}
	external := &http.Transport{Proxy: http.ProxyURL(pu), TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: func() *x509.CertPool { p := x509.NewCertPool(); p.AppendCertsFromPEM(ca.pem); return p }()}, ForceAttemptHTTP2: true}
	defer external.CloseIdleConnections()
	client := &http.Client{Transport: external, Timeout: 5 * time.Second}
	for _, credential := range []string{"", "Bearer invalid-connect-key"} {
		request, _ := http.NewRequest("GET", "https://"+apiHost+"/v1/models", nil)
		if credential != "" {
			request.Header.Set("Authorization", credential)
		}
		response, err := client.Do(request)
		if err != nil {
			t.Fatal("denied request failed to reach API admission")
		}
		io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if response.StatusCode != http.StatusUnauthorized || calls.Load() != 0 {
			t.Fatal("runtime API authority did not reject credential before origin")
		}
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		req, _ := http.NewRequest("GET", "https://"+apiHost+"/v1/models", nil)
		req.Header.Set("Authorization", "Bearer "+issued.Secret)
		res, e := client.Do(req)
		if e == nil && res.StatusCode == 200 {
			body, _ := io.ReadAll(res.Body)
			res.Body.Close()
			if res.ProtoMajor != 2 || string(body) != "runtime-ok" || calls.Load() != 1 {
				t.Fatalf("proto/response/body/calls %s/%q/%d", res.Proto, body, calls.Load())
			}
			break
		}
		if res != nil {
			res.Body.Close()
		}
		if time.Now().After(deadline) {
			t.Fatalf("runtime API not ready: %v", e)
		}
		time.Sleep(100 * time.Millisecond)
	}
	connector.Close()
	select {
	case <-connector.Done():
	default:
		t.Fatal("connector cleanup did not complete")
	}
	gateway.Close()
	select {
	case <-gateway.Done():
	default:
		t.Fatal("gateway cleanup did not complete")
	}
	for _, name := range []string{"admin.sock", "ingress.sock"} {
		if _, err := os.Lstat(filepath.Join(gatewayCfg.StateDirectory, name)); !os.IsNotExist(err) {
			t.Fatal("owned Unix socket survived cleanup")
		}
	}
	// These were actually opened by the native origin and tunnel processes.
	// Successful rebinding after Close proves they no longer retain listeners.
	for _, address := range []string{connectorCfg.Resources[0].Envelope.Listen, gatewayCfg.TunnelListen, gatewayCfg.Gateway.Resources[0].TunnelAddress} {
		listener, err := net.Listen("tcp4", address)
		if err != nil {
			t.Fatalf("owned listener survived cleanup: %v", err)
		}
		listener.Close()
	}
}

func connectProxy(t *testing.T, target string, allowed map[string]bool) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodConnect || !allowed[r.Host] {
			http.Error(w, "denied", 403)
			return
		}
		up, err := net.DialTimeout("tcp4", target, time.Second)
		if err != nil {
			http.Error(w, "upstream", 502)
			return
		}
		defer up.Close()
		down, bw, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer down.Close()
		bw.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n")
		bw.Flush()
		done := make(chan struct{})
		go func() { io.Copy(up, bw); up.Close(); close(done) }()
		io.Copy(down, up)
		<-done
	})
}
