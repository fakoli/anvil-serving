package transport

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	connect "github.com/fakoli/anvil-serving/connect"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"golang.org/x/sys/unix"
)

var ErrProcess = errors.New("managed tunnel unavailable or invalid")

// Process owns exactly the child it started. Child output is discarded because
// upstream diagnostics may include a request or credentials; status is metadata.
type Process struct {
	pid    int
	cancel context.CancelFunc
	done   chan struct{}
	mu     sync.Mutex
	failed bool
}

func (p *Process) PID() int              { return p.pid }
func (p *Process) Done() <-chan struct{} { return p.done }
func (p *Process) Failed() bool          { p.mu.Lock(); defer p.mu.Unlock(); return p.failed }
func (p *Process) Close() error {
	p.cancel()
	select {
	case <-p.done:
		return nil
	case <-time.After(5 * time.Second):
		return ErrProcess
	}
}

func pinnedFile(path string) (*os.File, error) {
	if runtime.GOOS != "linux" || !filepath.IsAbs(path) {
		return nil, ErrProcess
	}
	f, err := os.OpenFile(path, os.O_RDONLY|unix.O_NOFOLLOW, 0)
	if err != nil {
		return nil, ErrProcess
	}
	deny := func() (*os.File, error) { f.Close(); return nil, ErrProcess }
	info, err := f.Stat()
	var stat unix.Stat_t
	if err != nil || !info.Mode().IsRegular() || info.Size() > 32*1024*1024 || info.Mode().Perm()&0022 != 0 || info.Mode().Perm()&0111 == 0 || info.Mode()&(os.ModeSetuid|os.ModeSetgid) != 0 || unix.Fstat(int(f.Fd()), &stat) != nil || (stat.Uid != 0 && stat.Uid != uint32(os.Geteuid())) {
		return deny()
	}
	var manifest struct {
		Artifacts map[string]struct {
			BinarySHA256 string `json:"binary_sha256"`
		} `json:"artifacts"`
	}
	if json.Unmarshal(connect.TransportManifest(), &manifest) != nil {
		return deny()
	}
	pin, ok := manifest.Artifacts[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		return deny()
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, io.LimitReader(f, 32*1024*1024+1)); err != nil || hex.EncodeToString(hash.Sum(nil)) != pin.BinarySHA256 {
		return deny()
	}
	return f, nil
}

func openInput(path string, private bool) (*os.File, error) {
	if !filepath.IsAbs(path) {
		return nil, ErrProcess
	}
	f, err := os.OpenFile(path, os.O_RDONLY|unix.O_NOFOLLOW, 0)
	if err != nil {
		return nil, ErrProcess
	}
	if !validInput(f, private) {
		f.Close()
		return nil, ErrProcess
	}
	return f, nil
}

func validInput(f *os.File, private bool) bool {
	info, err := f.Stat()
	var stat unix.Stat_t
	if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 1024*1024 || info.Mode().Perm()&0022 != 0 || unix.Fstat(int(f.Fd()), &stat) != nil || (stat.Uid != 0 && stat.Uid != uint32(os.Geteuid())) {
		return false
	}
	return !private || (info.Mode().Perm() == 0600 && stat.Uid == uint32(os.Geteuid()) && stat.Nlink == 1)
}

type inputs struct{ files []*os.File }

func (s *inputs) close() {
	for _, f := range s.files {
		f.Close()
	}
}
func (s *inputs) hold(f *os.File) string {
	path := "/proc/self/fd/" + strconv.Itoa(4+len(s.files)) // descriptor 3 is the binary
	s.files = append(s.files, f)
	return path
}
func (s *inputs) file(path string, private bool) (string, error) {
	f, err := openInput(path, private)
	if err != nil {
		return "", err
	}
	return s.hold(f), nil
}
func (s *inputs) directory(path string, empty bool) (*os.File, string, error) {
	if !filepath.IsAbs(path) {
		return nil, "", ErrProcess
	}
	f, err := os.OpenFile(path, os.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW, 0)
	if err != nil {
		return nil, "", ErrProcess
	}
	info, err := f.Stat()
	var stat unix.Stat_t
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 || unix.Fstat(int(f.Fd()), &stat) != nil || stat.Uid != uint32(os.Geteuid()) {
		f.Close()
		return nil, "", ErrProcess
	}
	if empty {
		entries, err := f.ReadDir(1)
		if (err != nil && err != io.EOF) || len(entries) != 0 {
			f.Close()
			return nil, "", ErrProcess
		}
	}
	return f, s.hold(f), nil
}
func (s *inputs) rotatingHeaders(path string) (string, error) {
	// wstunnel rereads this file on each connection. Pin the owned directory,
	// allowing only the owner to atomically replace its validated 0600 file.
	dir, child, err := s.directory(filepath.Dir(path), false)
	if err != nil {
		return "", err
	}
	name := filepath.Base(path)
	fd, err := unix.Openat(int(dir.Fd()), name, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return "", ErrProcess
	}
	f := os.NewFile(uintptr(fd), name)
	defer f.Close()
	if !validInput(f, true) {
		return "", ErrProcess
	}
	return child + "/" + name, nil
}

func start(ctx context.Context, binary string, env, args []string, files []*os.File) (*Process, error) {
	if ctx == nil || ctx.Err() != nil {
		return nil, ErrProcess
	}
	f, err := pinnedFile(binary)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	childContext, cancel := context.WithCancel(ctx)
	// Execute the verified inode through the child descriptor, avoiding an
	// executable path replacement between digest verification and exec.
	cmd := exec.CommandContext(childContext, "/proc/self/fd/3", args...)
	cmd.Args[0] = "wstunnel"
	cmd.ExtraFiles = append([]*os.File{f}, files...)
	cmd.Env = append([]string{"PATH=/usr/bin:/bin"}, env...)
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	cmd.Cancel = func() error { return cmd.Process.Signal(syscall.SIGTERM) }
	cmd.WaitDelay = 3 * time.Second
	if err := cmd.Start(); err != nil {
		cancel()
		return nil, ErrProcess
	}
	p := &Process{pid: cmd.Process.Pid, cancel: cancel, done: make(chan struct{})}
	go func() {
		err := cmd.Wait()
		p.mu.Lock()
		p.failed = err != nil && childContext.Err() == nil
		p.mu.Unlock()
		close(p.done)
		cancel()
	}()
	return p, nil
}

type ServerOptions struct {
	Binary, Listen, CertificateFile, PrivateKeyFile, ClientCAFile, RestrictionsFile string
}

func StartServer(ctx context.Context, o ServerOptions) (*Process, error) {
	if !config.LoopbackAddress(o.Listen) {
		return nil, ErrProcess
	}
	var in inputs
	defer in.close()
	certificate, err := in.file(o.CertificateFile, false)
	if err != nil {
		return nil, err
	}
	key, err := in.file(o.PrivateKeyFile, true)
	if err != nil {
		return nil, err
	}
	roots, err := in.file(o.ClientCAFile, false)
	if err != nil {
		return nil, err
	}
	restrictions, err := in.file(o.RestrictionsFile, true)
	if err != nil {
		return nil, err
	}
	return start(ctx, o.Binary, nil, []string{"server", "--no-color", "--log-lvl", "warn", "--nb-worker-threads", "1", "--tls-certificate", certificate, "--tls-private-key", key, "--tls-client-ca-certs", roots, "--restrict-config", restrictions, "wss://" + o.Listen}, in.files)
}

type ClientOptions struct {
	ViaGate                                                         bool
	Binary, ServerURL, ReverseAddress, OriginAddress                string
	CertificateFile, PrivateKeyFile, TrustFile, EmptyTrustDirectory string
	HeadersFile, ProxyURL                                           string
}

func StartClient(ctx context.Context, o ClientOptions) (*Process, error) {
	u, err := url.Parse(o.ServerURL)
	if err != nil || u.Scheme != "wss" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || strings.ContainsAny(o.ServerURL, "?#") || (u.Path != "" && u.Path != "/") || !config.LoopbackAddress(o.ReverseAddress) || !config.LoopbackAddress(o.OriginAddress) {
		return nil, ErrProcess
	}
	var in inputs
	defer in.close()
	certificate, err := in.file(o.CertificateFile, false)
	if err != nil {
		return nil, err
	}
	key, err := in.file(o.PrivateKeyFile, true)
	if err != nil {
		return nil, err
	}
	trust, err := in.file(o.TrustFile, false)
	if err != nil {
		return nil, err
	}
	_, empty, err := in.directory(o.EmptyTrustDirectory, true)
	if err != nil {
		return nil, err
	}
	args := []string{"client", "--no-color", "--log-lvl", "warn", "--nb-worker-threads", "1", "--tls-verify-certificate", "--tls-certificate", certificate, "--tls-private-key", key, "--connection-retry-max-backoff", "1s", "--reverse-tunnel-connection-retry-max-backoff", "1s", "-R", "tcp://" + o.ReverseAddress + ":" + o.OriginAddress}
	if o.ViaGate {
		args = append(args, "--http-upgrade-path-prefix", "acv1")
	}
	if o.HeadersFile != "" {
		headers, err := in.rotatingHeaders(o.HeadersFile)
		if err != nil {
			return nil, err
		}
		args = append(args, "--http-headers-file", headers)
	}
	if o.ProxyURL != "" {
		proxy, err := url.Parse(o.ProxyURL)
		if err != nil || proxy.Scheme != "http" || proxy.Host == "" || proxy.User != nil || proxy.Path != "" || strings.ContainsAny(o.ProxyURL, "?#") {
			return nil, ErrProcess
		}
		args = append(args, "--http-proxy", o.ProxyURL)
	}
	args = append(args, o.ServerURL)
	return start(ctx, o.Binary, []string{"SSL_CERT_FILE=" + trust, "SSL_CERT_DIR=" + empty}, args, in.files)
}
