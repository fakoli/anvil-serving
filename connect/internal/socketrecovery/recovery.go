// Package socketrecovery preserves refused gateway socket leftovers. Callers
// must hold the gateway's exclusive authority-store lock and retain the verified
// directory descriptors until all candidates have been checked and recovered.
package socketrecovery

import (
	"errors"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

var ErrRecovery = errors.New("gateway socket recovery refused")

// Refused retains an exact socket identity, not permission to remove a later
// occupant. A nil candidate means no entry existed; Listen still rejects races.
type Refused struct {
	path      string
	directory unix.Stat_t
	socket    unix.Stat_t
}

func socketIdentity(path string, uid, gid, mode uint32) (unix.Stat_t, error) {
	var current unix.Stat_t
	if err := unix.Lstat(path, &current); err != nil {
		return current, err
	}
	if current.Mode != unix.S_IFSOCK|mode || current.Uid != uid || current.Gid != gid || current.Nlink != 1 {
		return unix.Stat_t{}, ErrRecovery
	}
	return current, nil
}

func sameSocket(left, right unix.Stat_t) bool {
	return left.Dev == right.Dev && left.Ino == right.Ino && left.Mode == right.Mode && left.Uid == right.Uid && left.Gid == right.Gid && left.Nlink == right.Nlink && left.Ctim == right.Ctim
}

func refused(path string) error {
	connection, err := net.DialTimeout("unix", path, time.Second)
	if connection != nil {
		_ = connection.Close()
	}
	if !errors.Is(err, unix.ECONNREFUSED) {
		return ErrRecovery
	}
	return nil
}

// Inspect accepts only the two gateway socket names beneath a pinned descriptor.
// Directory ancestry remains the caller's existing private/ingress policy.
func Inspect(path string, uid, gid, mode uint32) (*Refused, error) {
	parts := strings.Split(strings.TrimPrefix(path, "/proc/self/fd/"), "/")
	if !strings.HasPrefix(path, "/proc/self/fd/") || len(parts) != 2 || uid != uint32(os.Geteuid()) {
		return nil, ErrRecovery
	}
	fd, err := strconv.Atoi(parts[0])
	if err != nil || fd < 0 || strconv.Itoa(fd) != parts[0] {
		return nil, ErrRecovery
	}
	var directory unix.Stat_t
	if unix.Fstat(fd, &directory) != nil || directory.Mode&unix.S_IFMT != unix.S_IFDIR || directory.Uid != uid || directory.Gid != gid {
		return nil, ErrRecovery
	}
	if !(parts[1] == "admin.sock" && mode == 0600 && directory.Mode&07777 == 0700 || parts[1] == "ingress.sock" && mode == 0660 && directory.Mode&07777 == 02710) {
		return nil, ErrRecovery
	}
	socket, err := socketIdentity(path, uid, gid, mode)
	if errors.Is(err, unix.ENOENT) {
		return nil, nil
	}
	if err != nil || refused(path) != nil {
		return nil, ErrRecovery
	}
	after, err := socketIdentity(path, uid, gid, mode)
	if err != nil || !sameSocket(socket, after) {
		return nil, ErrRecovery
	}
	return &Refused{path: path, directory: directory, socket: socket}, nil
}

// Quarantine rechecks liveness and identity before moving a leftover into a
// fresh owner-private directory on the same mount. It never deletes an entry,
// overwrites a destination, or restores over a new listener after failure.
func (r *Refused) Quarantine() error {
	if r == nil {
		return nil
	}
	parent := filepath.Dir(r.path)
	var directory unix.Stat_t
	if unix.Stat(parent, &directory) != nil || directory.Dev != r.directory.Dev || directory.Ino != r.directory.Ino || directory.Mode != r.directory.Mode || directory.Uid != r.directory.Uid || directory.Gid != r.directory.Gid {
		return ErrRecovery
	}
	current, err := socketIdentity(r.path, r.socket.Uid, r.socket.Gid, r.socket.Mode&07777)
	if err != nil || !sameSocket(current, r.socket) || refused(r.path) != nil {
		return ErrRecovery
	}
	quarantine, err := os.MkdirTemp(parent, "refused-sockets-")
	if err != nil {
		return ErrRecovery
	}
	var created unix.Stat_t
	if unix.Lstat(quarantine, &created) != nil || created.Mode&unix.S_IFMT != unix.S_IFDIR || created.Uid != r.socket.Uid || created.Gid != r.directory.Gid || (created.Mode&07777 != 0700 && created.Mode&07777 != 02700) {
		return ErrRecovery
	}
	destination, err := unix.Open(quarantine, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return ErrRecovery
	}
	defer unix.Close(destination)
	var target unix.Stat_t
	if unix.Fstat(destination, &target) != nil || target.Dev != created.Dev || target.Ino != created.Ino || target.Mode != created.Mode || target.Uid != created.Uid || target.Gid != created.Gid {
		return ErrRecovery
	}
	// A setgid ingress parent propagates its setgid bit to new directories.
	// Quarantine is owner-private, with no inherited group access or flags.
	if unix.Fchmod(destination, 0700) != nil {
		return ErrRecovery
	}
	if unix.Fstat(destination, &target) != nil || target.Mode&07777 != 0700 || target.Uid != r.socket.Uid {
		return ErrRecovery
	}
	current, err = socketIdentity(r.path, r.socket.Uid, r.socket.Gid, r.socket.Mode&07777)
	if err != nil || !sameSocket(current, r.socket) {
		return ErrRecovery
	}
	if unix.Renameat2(unix.AT_FDCWD, r.path, destination, filepath.Base(r.path), unix.RENAME_NOREPLACE) != nil {
		return ErrRecovery
	}
	var preserved unix.Stat_t
	if unix.Fstatat(destination, filepath.Base(r.path), &preserved, unix.AT_SYMLINK_NOFOLLOW) != nil || preserved.Dev != r.socket.Dev || preserved.Ino != r.socket.Ino || preserved.Mode != r.socket.Mode || preserved.Uid != r.socket.Uid || preserved.Gid != r.socket.Gid || preserved.Nlink != 1 {
		return ErrRecovery // Retain unknown evidence; never overwrite a new name.
	}
	if unix.Fsync(destination) != nil {
		return ErrRecovery
	}
	parentFD, err := unix.Open(parent, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return ErrRecovery
	}
	defer unix.Close(parentFD)
	if unix.Fsync(parentFD) != nil {
		return ErrRecovery
	}
	return nil
}
