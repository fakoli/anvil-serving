// Package lab qualifies an upstream transport against synthetic, loopback-only
// services. It does not operate the installed router or any model service.
package lab

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

type transportLock struct {
	Schema    string `json:"schema"`
	Name      string `json:"name"`
	Version   string `json:"version"`
	License   string `json:"license"`
	Source    string `json:"source"`
	Release   string `json:"release"`
	Artifacts map[string]struct {
		URL           string `json:"url"`
		ArchiveSHA256 string `json:"archive_sha256"`
		BinarySHA256  string `json:"binary_sha256"`
	} `json:"artifacts"`
}

func loadLock(t *testing.T) transportLock {
	t.Helper()
	f, err := os.Open("../transport.lock.json")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var lock transportLock
	decoder := json.NewDecoder(io.LimitReader(f, 16384))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&lock); err != nil {
		t.Fatal(err)
	}
	if lock.Schema != "anvil-connect.transport-lock/v1" || lock.Name != "wstunnel" {
		t.Fatal("unsupported transport lock")
	}
	return lock
}

func verifyDigest(path, expected string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, f); err != nil {
		return err
	}
	if hex.EncodeToString(hash.Sum(nil)) != expected {
		return fmt.Errorf("transport binary digest mismatch")
	}
	return nil
}

func pinnedBinary(t *testing.T) string {
	t.Helper()
	lock := loadLock(t)
	artifact, ok := lock.Artifacts[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		t.Fatalf("no qualified transport artifact for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	path := os.Getenv("ANVIL_CONNECT_WSTUNNEL")
	if path == "" {
		t.Fatal("set ANVIL_CONNECT_WSTUNNEL to the downloaded transport binary; the lab does not install software or silently skip qualification")
	}
	if err := verifyDigest(path, artifact.BinarySHA256); err != nil {
		t.Fatal(err)
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		t.Fatal(err)
	}
	return abs
}

type boundedLog struct {
	sync.Mutex
	data []byte
}

func (b *boundedLog) Write(p []byte) (int, error) {
	b.Lock()
	defer b.Unlock()
	const limit = 64 * 1024
	if len(p) >= limit {
		b.data = append(b.data[:0], p[len(p)-limit:]...)
	} else {
		if excess := len(b.data) + len(p) - limit; excess > 0 {
			b.data = b.data[excess:]
		}
		b.data = append(b.data, p...)
	}
	return len(p), nil
}

func (b *boundedLog) String() string {
	b.Lock()
	defer b.Unlock()
	return string(b.data)
}

type child struct {
	t    *testing.T
	cmd  *exec.Cmd
	log  *boundedLog
	done chan struct{}
	once sync.Once
}

func startChild(t *testing.T, binary string, env []string, args ...string) *child {
	t.Helper()
	c := &child{t: t, cmd: exec.Command(binary, args...), log: &boundedLog{}, done: make(chan struct{})}
	// Do not inherit proxy, credential, or tracing variables from the operator.
	c.cmd.Env = append([]string{"PATH=" + os.Getenv("PATH")}, env...)
	c.cmd.Stdout, c.cmd.Stderr = c.log, c.log
	if err := c.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	go func() { _ = c.cmd.Wait(); close(c.done) }()
	t.Cleanup(c.stop)
	return c
}

func (c *child) stop() {
	c.once.Do(func() {
		if err := c.cmd.Process.Kill(); err != nil && !errors.Is(err, os.ErrProcessDone) {
			c.t.Errorf("transport cleanup kill failed: %v", err)
		}
		select {
		case <-c.done:
		case <-time.After(5 * time.Second):
			c.t.Error("transport cleanup did not reap child within 5 seconds")
		}
	})
}

func loopbackPort(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := fmt.Sprint(l.Addr().(*net.TCPAddr).Port)
	if err := l.Close(); err != nil {
		t.Fatal(err)
	}
	return port
}

// wstunnel cannot inherit a prebound FD. A port allocation collision must fail
// qualification rather than accepting another process's listener as ours.
// The lock qualifies Linux only, so inspect socket ownership on our child.
func ownsListener(address string, process *child) (bool, error) {
	return ownsPIDListener(address, process.cmd.Process.Pid)
}

func ownsPIDListener(address string, pid int) (bool, error) {
	host, portText, err := net.SplitHostPort(address)
	if err != nil || host != "127.0.0.1" {
		return false, fmt.Errorf("unexpected lab listener address")
	}
	port, err := strconv.Atoi(portText)
	if err != nil {
		return false, err
	}
	root := fmt.Sprintf("/proc/%d", pid)
	fds, err := os.ReadDir(root + "/fd")
	if err != nil {
		return false, err
	}
	sockets := map[string]bool{}
	for _, fd := range fds {
		link, err := os.Readlink(root + "/fd/" + fd.Name())
		if err == nil && strings.HasPrefix(link, "socket:[") {
			sockets[strings.TrimSuffix(strings.TrimPrefix(link, "socket:["), "]")] = true
		}
	}
	tcp, err := os.ReadFile(root + "/net/tcp")
	if err != nil {
		return false, err
	}
	wanted := fmt.Sprintf("0100007F:%04X", port)
	for _, line := range strings.Split(string(tcp), "\n") {
		fields := strings.Fields(line)
		if len(fields) > 9 && fields[1] == wanted && fields[3] == "0A" && sockets[fields[9]] {
			return true, nil
		}
	}
	return false, nil
}

func awaitListener(t *testing.T, address string, process, owner *child) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp4", address, 50*time.Millisecond)
		if err == nil {
			_ = conn.Close()
			owned, err := ownsListener(address, owner)
			if err != nil {
				t.Fatalf("cannot verify transport listener ownership: %v", err)
			}
			if owned {
				return
			}
		}
		select {
		case <-process.done:
			t.Fatalf("transport exited before binding: %s", process.log.String())
		default:
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("transport did not own loopback listener %s (possible port collision): %s", address, process.log.String())
}

type authority struct {
	cert *x509.Certificate
	key  *ecdsa.PrivateKey
	pem  []byte
}

func certificate(t *testing.T, template, parent *x509.Certificate, public any, signer *ecdsa.PrivateKey) []byte {
	t.Helper()
	der, err := x509.CreateCertificate(rand.Reader, template, parent, public, signer)
	if err != nil {
		t.Fatal(err)
	}
	return der
}

func newAuthority(t *testing.T) authority {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: "Anvil Connect lab CA " + serial.Text(16)}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der := certificate(t, template, template, &key.PublicKey, key)
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return authority{parsed, key, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})}
}

type identity struct {
	certPath, keyPath string
	pair              tls.Certificate
}

func (ca authority) issue(t *testing.T, dir, name string, server bool) identity {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: name}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
	if server {
		template.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		template.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
	}
	der := certificate(t, template, ca.cert, &key.PublicKey, ca.key)
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
	result := identity{certPath: filepath.Join(dir, name+".pem"), keyPath: filepath.Join(dir, name+".key")}
	if err := os.WriteFile(result.certPath, certPEM, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(result.keyPath, keyPEM, 0600); err != nil {
		t.Fatal(err)
	}
	result.pair, err = tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

type fixture struct {
	binary, dir, caPath, serverAddress, reversePort string
	ca                                              authority
	client                                          identity
	server                                          *child
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	f := &fixture{binary: pinnedBinary(t), dir: t.TempDir(), ca: newAuthority(t), reversePort: loopbackPort(t)}
	f.caPath = filepath.Join(f.dir, "ca.pem")
	if err := os.WriteFile(f.caPath, f.ca.pem, 0600); err != nil {
		t.Fatal(err)
	}
	serverID := f.ca.issue(t, f.dir, "gateway", true)
	f.client = f.ca.issue(t, f.dir, "connector-a", false)
	f.serverAddress = "127.0.0.1:" + loopbackPort(t)
	rules := fmt.Sprintf("restrictions:\n  - name: resource-a\n    match:\n      - !PathPrefix '^connector-a$'\n    allow:\n      - !ReverseTunnel\n        protocol: [Tcp]\n        port: [%s]\n        cidr: [127.0.0.1/32]\n", f.reversePort)
	restrictions := filepath.Join(f.dir, "restrictions.yaml")
	if err := os.WriteFile(restrictions, []byte(rules), 0600); err != nil {
		t.Fatal(err)
	}
	f.server = startChild(t, f.binary, nil, "server", "--no-color", "--log-lvl", "warn", "--nb-worker-threads", "1", "--tls-certificate", serverID.certPath, "--tls-private-key", serverID.keyPath, "--tls-client-ca-certs", f.caPath, "--restrict-config", restrictions, "wss://"+f.serverAddress)
	awaitListener(t, f.serverAddress, f.server, f.server)
	return f
}

func (f *fixture) connector(t *testing.T, target, bind string, id identity, trust string, options ...string) *child {
	t.Helper()
	empty := filepath.Join(f.dir, "empty-roots")
	if err := os.MkdirAll(empty, 0700); err != nil {
		t.Fatal(err)
	}
	args := []string{"client", "--no-color", "--log-lvl", "warn", "--nb-worker-threads", "1", "--tls-verify-certificate", "--tls-certificate", id.certPath, "--tls-private-key", id.keyPath, "--connection-retry-max-backoff", "1s", "--reverse-tunnel-connection-retry-max-backoff", "1s", "-R", "tcp://127.0.0.1:" + bind + ":" + target}
	args = append(args, options...)
	args = append(args, "wss://"+f.serverAddress)
	return startChild(t, f.binary, []string{"SSL_CERT_FILE=" + trust, "SSL_CERT_DIR=" + empty}, args...)
}

func TestPinnedBinary(t *testing.T) {
	path := pinnedBinary(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	output, err := exec.CommandContext(ctx, path, "--version").Output()
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(output)) != "wstunnel-cli "+loadLock(t).Version {
		t.Fatalf("unexpected binary version: %s", output)
	}
	if err := verifyDigest(path, strings.Repeat("0", 64)); err == nil {
		t.Fatal("mismatched digest accepted")
	}
	t.Logf("verified pinned %s", strings.TrimSpace(string(output)))
}

func TestTLSMutualAuthentication(t *testing.T) {
	f := newFixture(t)
	unknown := newAuthority(t)
	badClient := unknown.issue(t, f.dir, "untrusted", false)
	roots := x509.NewCertPool()
	roots.AppendCertsFromPEM(f.ca.pem)
	for _, tc := range []struct {
		name  string
		roots *x509.CertPool
		certs []tls.Certificate
		allow bool
	}{
		{"trusted", roots, []tls.Certificate{f.client.pair}, true},
		{"missing-client", roots, nil, false},
		{"unknown-client", roots, []tls.Certificate{badClient.pair}, false},
		{"unknown-server", x509.NewCertPool(), []tls.Certificate{f.client.pair}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: tc.roots, Certificates: tc.certs}}
			defer transport.CloseIdleConnections()
			client := &http.Client{Transport: transport, Timeout: 2 * time.Second}
			response, err := client.Get("https://" + f.serverAddress + "/")
			if response != nil {
				response.Body.Close()
			}
			if tc.allow && err != nil {
				t.Fatalf("trusted TLS refused: %v", err)
			}
			if !tc.allow && err == nil {
				t.Fatal("untrusted TLS accepted")
			}
		})
	}
}

func TestTLSConnectorVerifiesServer(t *testing.T) {
	f := newFixture(t)
	origin, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer origin.Close()
	good := f.connector(t, origin.Addr().String(), f.reversePort, f.client, f.caPath)
	awaitListener(t, "127.0.0.1:"+f.reversePort, good, f.server)
	good.stop()
	// A separate fixture avoids the reverse listener's documented idle lifetime.
	other := newFixture(t)
	wrongCA := filepath.Join(other.dir, "wrong-ca.pem")
	if err := os.WriteFile(wrongCA, newAuthority(t).pem, 0600); err != nil {
		t.Fatal(err)
	}
	bad := other.connector(t, origin.Addr().String(), other.reversePort, other.client, wrongCA)
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		// The client's connection pool reports only TimedOut; the accepting
		// server observes the specific TLS alert sent by the rejecting client.
		if bytes.Contains(bytes.ToLower([]byte(other.server.log.String())), []byte("unknownca")) {
			conn, err := net.DialTimeout("tcp4", "127.0.0.1:"+other.reversePort, 100*time.Millisecond)
			if err == nil {
				conn.Close()
				t.Fatal("untrusted connector obtained a reverse listener")
			}
			bad.stop()
			// Restore only the trust root to demonstrate the same otherwise
			// healthy endpoint becomes usable once certificate trust is valid.
			recovered := other.connector(t, origin.Addr().String(), other.reversePort, other.client, other.caPath)
			awaitListener(t, "127.0.0.1:"+other.reversePort, recovered, other.server)
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("no explicit TLS verification rejection observed: client=%s server=%s", bad.log.String(), other.server.log.String())
}
