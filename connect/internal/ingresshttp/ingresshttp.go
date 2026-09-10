// Package ingresshttp provides the gateway-owned Unix listener used by the
// separately identified TLS edge. It is deliberately independent from the
// owner-only administrative listener and private authority directory.
package ingresshttp

import (
	"errors"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"

	"golang.org/x/sys/unix"
)

const socketName = "ingress.sock"
const maximumID = uint32(2147483647)

var ErrIngress = errors.New("ingress listener unavailable")

// Policy identifies the one gateway process, edge process, group, and shared
// runtime directory allowed to participate in this local ingress boundary.
type Policy struct {
	GatewayUID uint32 `json:"gateway_uid"`
	EdgeUID    uint32 `json:"edge_uid"`
	GroupID    uint32 `json:"group_id"`
	Directory  string `json:"directory"`
}

// Validate checks only declaration syntax and numeric policy. Filesystem and
// process identity checks are performed by Listen.
func (p Policy) Validate() error {
	if p.GatewayUID == 0 || p.GatewayUID > maximumID || p.EdgeUID == 0 || p.EdgeUID > maximumID || p.GroupID == 0 || p.GroupID > maximumID || p.GatewayUID == p.EdgeUID || !validDirectory(p.Directory) {
		return ErrIngress
	}
	return nil
}

func validDirectory(value string) bool {
	if value == "" || value == "/" || !filepath.IsAbs(value) || filepath.Clean(value) != value {
		return false
	}
	return !strings.ContainsFunc(value, func(r rune) bool { return r < 0x20 || r == 0x7f })
}

type retainedDirectory struct {
	file *os.File
	fd   int
	stat unix.Stat_t
}

func openDirectory(policy Policy) (*retainedDirectory, error) {
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrIngress
	}
	closeFD := func() (*retainedDirectory, error) {
		_ = unix.Close(fd)
		return nil, ErrIngress
	}
	var current unix.Stat_t
	if unix.Fstat(fd, &current) != nil || !validAncestor(&current) {
		return closeFD()
	}
	parts := strings.Split(strings.TrimPrefix(policy.Directory, "/"), "/")
	for index, part := range parts {
		next, err := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		if err != nil {
			return closeFD()
		}
		_ = unix.Close(fd)
		fd = next
		if unix.Fstat(fd, &current) != nil {
			return closeFD()
		}
		if index == len(parts)-1 {
			if !validLeaf(&current, policy) {
				return closeFD()
			}
		} else if !validAncestor(&current) {
			return closeFD()
		}
	}
	return &retainedDirectory{file: os.NewFile(uintptr(fd), socketName), fd: fd, stat: current}, nil
}

func validAncestor(stat *unix.Stat_t) bool {
	return stat != nil && stat.Mode&unix.S_IFMT == unix.S_IFDIR && stat.Uid == 0 && stat.Mode&0022 == 0
}

func validLeaf(stat *unix.Stat_t, policy Policy) bool {
	return stat != nil && stat.Mode&unix.S_IFMT == unix.S_IFDIR && stat.Mode&07777 == 02710 && stat.Uid == policy.GatewayUID && stat.Gid == policy.GroupID
}

func (d *retainedDirectory) Close() error {
	if d == nil || d.file == nil || d.file.Close() != nil {
		return ErrIngress
	}
	return nil
}

func pinnedSocketPath(fd int) (string, error) {
	if fd < 0 {
		return "", ErrIngress
	}
	path := "/proc/self/fd/" + strconv.Itoa(fd) + "/" + socketName
	if len(path) > 107 {
		return "", ErrIngress
	}
	return path, nil
}

func noExistingSocket(fd int) bool {
	var stat unix.Stat_t
	return errors.Is(unix.Fstatat(fd, socketName, &stat, unix.AT_SYMLINK_NOFOLLOW), unix.ENOENT)
}

func validSocket(stat *unix.Stat_t, policy Policy) bool {
	return stat != nil && stat.Mode&unix.S_IFMT == unix.S_IFSOCK && stat.Mode&07777 == 0660 && stat.Uid == policy.GatewayUID && stat.Gid == policy.GroupID
}

func ownedSocketStat(fd int, policy Policy) (unix.Stat_t, error) {
	var stat unix.Stat_t
	if unix.Fstatat(fd, socketName, &stat, unix.AT_SYMLINK_NOFOLLOW) != nil || stat.Mode&unix.S_IFMT != unix.S_IFSOCK || stat.Uid != policy.GatewayUID {
		return unix.Stat_t{}, ErrIngress
	}
	return stat, nil
}

func socketStat(fd int, policy Policy) (unix.Stat_t, error) {
	stat, err := ownedSocketStat(fd, policy)
	if err != nil || !validSocket(&stat, policy) {
		return unix.Stat_t{}, ErrIngress
	}
	return stat, nil
}

func matchesCurrentDirectory(policy Policy, expected unix.Stat_t) bool {
	current, err := openDirectory(policy)
	if err != nil {
		return false
	}
	defer current.Close()
	return current.stat.Dev == expected.Dev && current.stat.Ino == expected.Ino
}

// Listen creates ingress.sock in the policy directory. It never removes an
// existing entry, and accepts only the exact configured edge UID.
func Listen(policy Policy) (net.Listener, error) {
	if runtime.GOOS != "linux" || policy.Validate() != nil || uint32(os.Geteuid()) != policy.GatewayUID {
		return nil, ErrIngress
	}
	directory, err := openDirectory(policy)
	if err != nil {
		return nil, ErrIngress
	}
	fail := func(listener *net.UnixListener, socket unix.Stat_t) (net.Listener, error) {
		if listener != nil {
			listener.SetUnlinkOnClose(false)
			_ = listener.Close()
		}
		removeMatching(directory.fd, socket)
		_ = directory.Close()
		return nil, ErrIngress
	}
	if !noExistingSocket(directory.fd) {
		return fail(nil, unix.Stat_t{})
	}
	path, err := pinnedSocketPath(directory.fd)
	if err != nil {
		return fail(nil, unix.Stat_t{})
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		return fail(nil, unix.Stat_t{})
	}
	listener.SetUnlinkOnClose(false)
	// Capture an identity that proves this is our just-bound socket before any
	// later operation can remove it. Cleanup never unlinks an unknown entry.
	socket, err := ownedSocketStat(directory.fd, policy)
	if err != nil {
		return fail(listener, unix.Stat_t{})
	}
	// No connection reaches an application handler before this listener is
	// returned, and Accept always applies SO_PEERCRED. The verified setgid leaf
	// prevents peer replacement while the socket mode is narrowed here without
	// changing the process-wide umask.
	if unix.Fchmodat(directory.fd, socketName, 0660, unix.AT_SYMLINK_NOFOLLOW) != nil {
		return fail(listener, socket)
	}
	checkedSocket, err := socketStat(directory.fd, policy)
	if err != nil || checkedSocket.Dev != socket.Dev || checkedSocket.Ino != socket.Ino || !matchesCurrentDirectory(policy, directory.stat) {
		return fail(listener, socket)
	}
	socket = checkedSocket
	return &peerListener{listener: listener, directory: directory, socket: socket, edgeUID: policy.EdgeUID}, nil
}

type peerListener struct {
	listener  *net.UnixListener
	directory *retainedDirectory
	socket    unix.Stat_t
	edgeUID   uint32
	once      sync.Once
	err       error
}

func (l *peerListener) Accept() (net.Conn, error) {
	for {
		connection, err := l.listener.AcceptUnix()
		if err != nil {
			return nil, err
		}
		if peerUID(connection, l.edgeUID) {
			return connection, nil
		}
		_ = connection.Close()
	}
}

func (l *peerListener) Addr() net.Addr { return l.listener.Addr() }

func (l *peerListener) Close() error {
	l.once.Do(func() {
		if l.listener.Close() != nil {
			l.err = ErrIngress
		}
		removeMatching(l.directory.fd, l.socket)
		if l.directory.Close() != nil && l.err == nil {
			l.err = ErrIngress
		}
	})
	return l.err
}

func peerUID(connection *net.UnixConn, allowed uint32) bool {
	if connection == nil {
		return false
	}
	approved := false
	raw, err := connection.SyscallConn()
	if err != nil {
		return false
	}
	if raw.Control(func(fd uintptr) {
		credential, err := unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
		approved = err == nil && credential != nil && credential.Uid == allowed
	}) != nil {
		return false
	}
	return approved
}

func removeMatching(fd int, expected unix.Stat_t) {
	if expected.Ino == 0 {
		return
	}
	var current unix.Stat_t
	if unix.Fstatat(fd, socketName, &current, unix.AT_SYMLINK_NOFOLLOW) != nil || current.Dev != expected.Dev || current.Ino != expected.Ino || current.Mode&unix.S_IFMT != unix.S_IFSOCK {
		return
	}
	_ = unix.Unlinkat(fd, socketName, 0)
}
