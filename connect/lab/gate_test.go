package lab

import (
	"bufio"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	installationid "github.com/fakoli/anvil-serving/connect/internal/identity"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
	"github.com/fakoli/anvil-serving/connect/internal/tunnelgate"
	jose "github.com/go-jose/go-jose/v4"
)

func gateIdentity(t *testing.T) (*installationid.Manager, installationid.Installation) {
	t.Helper()
	state, err := store.Open(filepath.Join(t.TempDir(), "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { state.Close() })
	m, err := installationid.NewManager(state, "https://connect.example.test", []string{"router"})
	if err != nil {
		t.Fatal(err)
	}
	raw, invitation, err := m.Invite("origin-a", "connector", []string{"router"}, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	jwk, err := json.Marshal(jose.JSONWebKey{Key: public, Algorithm: string(jose.EdDSA), Use: "sig"})
	if err != nil {
		t.Fatal(err)
	}
	claims, err := installationid.NewAssertion("origin-a", "https://connect.example.test/enroll", "connector", "", invitation.Epoch, installationid.EnrollmentNonce(raw), 0, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	proof, err := installationid.Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	installation, err := m.Enroll(raw, jwk, proof)
	if err != nil {
		t.Fatal(err)
	}
	if err := m.Approve(installation.ID, installation.Fingerprint); err != nil {
		t.Fatal(err)
	}
	var nonce [32]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		t.Fatal(err)
	}
	challenge := hex.EncodeToString(nonce[:])
	claims, err = installationid.NewAssertion("origin-a", "https://connect.example.test/connector", "connector", "router", installation.Epoch, challenge, installation.Generation, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	proof, err = installationid.Sign(private, claims)
	if err != nil {
		t.Fatal(err)
	}
	verified, err := m.Verify(proof, "connector", "router", challenge)
	if err != nil {
		t.Fatal(err)
	}
	return m, verified
}

func gateCertFiles(t *testing.T, dir, name string, certificate tls.Certificate) (string, string) {
	t.Helper()
	cert, key := filepath.Join(dir, name+".pem"), filepath.Join(dir, name+"-key.pem")
	var data []byte
	for _, der := range certificate.Certificate {
		data = append(data, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})...)
	}
	private, err := x509.MarshalPKCS8PrivateKey(certificate.PrivateKey)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cert, data, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(key, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: private}), 0600); err != nil {
		t.Fatal(err)
	}
	return cert, key
}

func TestManagedTunnelGateOwnsAndRevokesRawStream(t *testing.T) {
	stopped := make(chan struct{})
	native := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(stopped)
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: through gate\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	t.Cleanup(native.Close)
	f, err := os.Open("../examples/connect.json")
	if err != nil {
		t.Fatal(err)
	}
	g, err := config.ReadGateway(f)
	f.Close()
	if err != nil {
		t.Fatal(err)
	}
	g.Resources[0].TunnelAddress = "127.0.0.1:" + loopbackPort(t)
	m, installation := gateIdentity(t)
	leases, err := tunnelgate.NewLeases(g, m, nil)
	if err != nil {
		t.Fatal(err)
	}
	credential, _, err := leases.Issue(installation, "router")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	backendAddress := "127.0.0.1:" + loopbackPort(t)
	backends := map[string]tunnelgate.Backend{"router": {Address: backendAddress, PathToken: strings.Repeat("b", 64), AuthToken: strings.Repeat("c", 64)}}
	ca := testpki.New(t)
	cert, key := gateCertFiles(t, dir, "backend", ca.Leaf(t, tunnelgate.BackendPeer, false))
	caPath := filepath.Join(dir, "backend-ca.pem")
	if err := os.WriteFile(caPath, ca.PEM(), 0600); err != nil {
		t.Fatal(err)
	}
	restrictions, err := tunnelgate.Restrictions(g, backends)
	if err != nil {
		t.Fatal(err)
	}
	restrictionFile := filepath.Join(dir, "restrictions.yaml")
	if err := os.WriteFile(restrictionFile, restrictions, 0600); err != nil {
		t.Fatal(err)
	}
	binary := pinnedBinary(t)
	server, err := transport.StartServer(context.Background(), transport.ServerOptions{Binary: binary, Listen: backendAddress, CertificateFile: cert, PrivateKeyFile: key, ClientCAFile: caPath, RestrictionsFile: restrictionFile})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
	})
	awaitManaged(t, backendAddress, server)
	gate, err := tunnelgate.New("tunnel.example.test", g, leases, backends, ca.Roots, ca.LeafWithCommonName(t, tunnelgate.GatePeer, backends["router"].PathToken, true))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(gate.Close)
	outer := newAuthority(t)
	outerID := outer.issue(t, dir, "public-gateway", true)
	outerCert, err := tls.LoadX509KeyPair(outerID.certPath, outerID.keyPath)
	if err != nil {
		t.Fatal(err)
	}
	front := httptest.NewUnstartedServer(gate)
	front.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{outerCert}}
	front.StartTLS()
	t.Cleanup(func() { front.CloseClientConnections(); front.Close() })
	clientID := outer.issue(t, dir, "origin-a", false)
	trust := filepath.Join(dir, "outer-ca.pem")
	if err := os.WriteFile(trust, outer.pem, 0600); err != nil {
		t.Fatal(err)
	}
	empty := filepath.Join(dir, "empty-roots")
	if err := os.Mkdir(empty, 0700); err != nil {
		t.Fatal(err)
	}
	headers := filepath.Join(dir, "headers.txt")
	if err := os.WriteFile(headers, []byte("Host: tunnel.example.test\nAuthorization: Bearer "+credential+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	client, err := transport.StartClient(context.Background(), transport.ClientOptions{ViaGate: true, Binary: binary, ServerURL: strings.Replace(front.URL, "https://", "wss://", 1), ReverseAddress: g.Resources[0].TunnelAddress, OriginAddress: native.Listener.Addr().String(), CertificateFile: clientID.certPath, PrivateKeyFile: clientID.keyPath, TrustFile: trust, EmptyTrustDirectory: empty, HeadersFile: headers})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := client.Close(); err != nil {
			t.Error(err)
		}
	})
	awaitManaged(t, g.Resources[0].TunnelAddress, server)
	// This fixture qualifies the transport gate. The ordinary API lab separately
	// requires the inner mTLS and native-token adapter at this reverse endpoint.
	httpClient := &http.Client{Timeout: 4 * time.Second}
	response, err := httpClient.Get("http://" + g.Resources[0].TunnelAddress + "/v1/events")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	reader := bufio.NewReader(response.Body)
	if line, err := reader.ReadString('\n'); err != nil || line != "data: through gate\n" {
		t.Fatal("raw gate stream failed", err)
	}
	started := time.Now()
	if err := m.Revoke(installation.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := io.ReadAll(reader); err == nil {
		t.Fatal("revoked transport completed normally")
	}
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("gate revocation did not close native stream")
	}
	if time.Since(started) > time.Second {
		t.Fatal("gate revocation exceeded local target")
	}
	t.Logf("installation revocation closed the gated raw tunnel in %s", time.Since(started))
	if _, err := leases.Authenticate(credential); err == nil {
		t.Fatal("revoked installation could open new tunnel")
	}
}
