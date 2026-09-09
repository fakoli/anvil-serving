// Package localhttp provides a same-UID Unix listener for Connect's native
// administrative surface. It does not authenticate HTTP requests; it filters
// Unix peers before a connection reaches net/http.
package localhttp

import (
	"errors"
	"net"
	"os"
	"runtime"
	"sync"

	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"golang.org/x/sys/unix"
)

var ErrLocal = errors.New("local administrative listener unavailable")

// Listen creates an owner-only Unix listener beneath directory's retained
// descriptor. It never unlinks an existing name: an old file or socket is a
// configuration failure requiring explicit local remediation.
func Listen(directory *privatefiles.Directory, name string) (net.Listener, error) {
	if runtime.GOOS != "linux" || directory == nil {
		return nil, ErrLocal
	}
	pinned, err := directory.PinPath(name)
	if err != nil {
		return nil, ErrLocal
	}
	path := pinned.Path()
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		_ = pinned.Close()
		return nil, ErrLocal
	}
	listener.SetUnlinkOnClose(false)
	if err := unix.Fchmodat(unix.AT_FDCWD, path, 0600, unix.AT_SYMLINK_NOFOLLOW); err != nil {
		_ = listener.Close()
		_ = pinned.Close()
		return nil, ErrLocal
	}
	stat, err := socketStat(path)
	if err != nil {
		_ = listener.Close()
		_ = pinned.Close()
		return nil, ErrLocal
	}
	return &peerListener{listener: listener, pinned: pinned, path: path, socket: stat}, nil
}

type peerListener struct {
	listener *net.UnixListener
	pinned   *privatefiles.PinnedPath // retain an independent directory FD for this listener's life
	path     string
	socket   unix.Stat_t
	once     sync.Once
}

func (l *peerListener) Accept() (net.Conn, error) {
	for {
		connection, err := l.listener.AcceptUnix()
		if err != nil {
			return nil, err
		}
		if sameUID(connection) {
			return connection, nil
		}
		_ = connection.Close()
		// A denied peer is one bad connection, not a server-wide failure.
	}
}

func (l *peerListener) Addr() net.Addr { return l.listener.Addr() }

func (l *peerListener) Close() error {
	var result error
	l.once.Do(func() {
		result = l.listener.Close()
		removeMatching(l.path, l.socket)
		if err := l.pinned.Close(); result == nil && err != nil {
			result = ErrLocal
		}
	})
	return result
}

func sameUID(connection *net.UnixConn) bool {
	allowed := false
	raw, err := connection.SyscallConn()
	if err != nil {
		return false
	}
	if raw.Control(func(fd uintptr) {
		credential, err := unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
		allowed = err == nil && credential != nil && credential.Uid == uint32(os.Geteuid())
	}) != nil {
		return false
	}
	return allowed
}

func socketStat(path string) (unix.Stat_t, error) {
	var stat unix.Stat_t
	if unix.Lstat(path, &stat) != nil || stat.Mode&unix.S_IFMT != unix.S_IFSOCK || stat.Mode&07777 != 0600 || stat.Uid != uint32(os.Geteuid()) {
		return unix.Stat_t{}, ErrLocal
	}
	return stat, nil
}

func removeMatching(path string, expected unix.Stat_t) {
	var actual unix.Stat_t
	if unix.Lstat(path, &actual) != nil || actual.Dev != expected.Dev || actual.Ino != expected.Ino || actual.Mode&unix.S_IFMT != unix.S_IFSOCK {
		return
	}
	_ = unix.Unlink(path)
}
