// Package privatefiles owns a small, private runtime state directory.
//
// It is intended for credentials that must be rotated without exposing a path
// traversal or symlink-following surface to callers. The directory descriptor,
// rather than the supplied pathname, remains the authority after Open.
package privatefiles

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"

	"golang.org/x/sys/unix"
)

const maxPayload = 1024 * 1024

var (
	// ErrPrivate intentionally carries no path, operating-system error, or file
	// contents. Callers can safely report it without leaking credential state.
	ErrPrivate = errors.New("private runtime state unavailable or invalid")
	ErrClosed  = errors.New("private runtime state is closed")
)

// Directory retains an opened, verified directory descriptor. Its methods are
// serialized so Close cannot race a descriptor operation.
type Directory struct {
	mu     sync.Mutex
	dir    *os.File
	fd     int
	closed bool
}

// Open opens (or creates) an owner-only directory for private runtime files.
// Parent path components may be symlinks (for example /data); the selected
// directory itself may not be a symlink and is checked again after opening.
func Open(path string) (*Directory, error) {
	if runtime.GOOS != "linux" || path == "" {
		return nil, ErrPrivate
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, ErrPrivate
	}
	absolute = filepath.Clean(absolute)
	if err := os.MkdirAll(absolute, 0700); err != nil {
		return nil, ErrPrivate
	}
	before, err := os.Lstat(absolute)
	if err != nil || !validDirectoryInfo(before) {
		return nil, ErrPrivate
	}
	f, err := os.OpenFile(absolute, os.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrPrivate
	}
	fail := func() (*Directory, error) {
		_ = f.Close()
		return nil, ErrPrivate
	}
	after, err := f.Stat()
	if err != nil || !validDirectoryInfo(after) || !os.SameFile(before, after) {
		return fail()
	}
	var stat unix.Stat_t
	if unix.Fstat(int(f.Fd()), &stat) != nil || stat.Uid != uint32(os.Geteuid()) || stat.Mode&07777 != 0700 {
		return fail()
	}
	return &Directory{dir: f, fd: int(f.Fd())}, nil
}

func validDirectoryInfo(info os.FileInfo) bool {
	if info == nil || !info.IsDir() || info.Mode().Perm() != 0700 {
		return false
	}
	return info.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) == 0
}

// Subdirectory opens or creates one owned child directory beneath the retained
// descriptor. It never resolves the original pathname and the returned child
// owns its descriptor independently of this directory's Close.
func (d *Directory) Subdirectory(name string) (*Directory, error) {
	if !validName(name) {
		return nil, ErrPrivate
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closed {
		return nil, ErrClosed
	}
	err := unix.Mkdirat(d.fd, name, 0700)
	if err != nil && !errors.Is(err, unix.EEXIST) {
		return nil, ErrPrivate
	}
	if err == nil && unix.Fsync(d.fd) != nil {
		return nil, ErrPrivate
	}
	var before unix.Stat_t
	if unix.Fstatat(d.fd, name, &before, unix.AT_SYMLINK_NOFOLLOW) != nil || before.Mode&unix.S_IFMT != unix.S_IFDIR || before.Mode&07777 != 0700 || before.Uid != uint32(os.Geteuid()) {
		return nil, ErrPrivate
	}
	fd, err := unix.Openat(d.fd, name, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrPrivate
	}
	var after unix.Stat_t
	if unix.Fstat(fd, &after) != nil || after.Dev != before.Dev || after.Ino != before.Ino || after.Mode&unix.S_IFMT != unix.S_IFDIR || after.Mode&07777 != 0700 || after.Uid != uint32(os.Geteuid()) {
		_ = unix.Close(fd)
		return nil, ErrPrivate
	}
	return &Directory{dir: os.NewFile(uintptr(fd), name), fd: fd}, nil
}

// PinnedPath owns a separate directory descriptor. Keep this handle open until
// every external pathname operation (including socket cleanup) has finished.
// Closing the originating Directory does not invalidate or retarget this path.
type PinnedPath struct {
	file  *os.File
	path  string
	close sync.Once
	err   error
}

func (p *PinnedPath) Path() string { return p.path }

func (p *PinnedPath) Close() error {
	p.close.Do(func() {
		if p.file.Close() != nil {
			p.err = ErrPrivate
		}
	})
	return p.err
}

// PinPath returns an independently pinned process-local pathname. The caller
// owns its handle and must not Close it while using Path, as with an os.File.
func (d *Directory) PinPath(name string) (*PinnedPath, error) {
	if !validName(name) {
		return nil, ErrPrivate
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closed {
		return nil, ErrClosed
	}
	fd, err := unix.FcntlInt(uintptr(d.fd), unix.F_DUPFD_CLOEXEC, 0)
	if err != nil {
		return nil, ErrPrivate
	}
	file := os.NewFile(uintptr(fd), name)
	path := "/proc/self/fd/" + strconv.Itoa(fd) + "/" + name
	// Linux sockaddr_un reserves one byte for the trailing NUL.
	if len(path) > 107 {
		_ = file.Close()
		return nil, ErrPrivate
	}
	return &PinnedPath{file: file, path: path}, nil
}

// Read returns at most max bytes from one validated private file.
func (d *Directory) Read(name string, max int) ([]byte, error) {
	if !validName(name) || max < 0 || max > maxPayload {
		return nil, ErrPrivate
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closed {
		return nil, ErrClosed
	}
	fd, err := unix.Openat(d.fd, name, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrPrivate
	}
	f := os.NewFile(uintptr(fd), name)
	defer f.Close()
	if !validFileFD(fd) {
		return nil, ErrPrivate
	}
	contents, err := io.ReadAll(io.LimitReader(f, int64(max)+1))
	if err != nil || len(contents) > max {
		return nil, ErrPrivate
	}
	return contents, nil
}

// Create writes data once. It never replaces an existing entry.
func (d *Directory) Create(name string, data []byte) error {
	if !validName(name) || len(data) > maxPayload {
		return ErrPrivate
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closed {
		return ErrClosed
	}
	fd, err := unix.Openat(d.fd, name, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0600)
	if err != nil {
		return ErrPrivate
	}
	var created unix.Stat_t
	ok := unix.Fstat(fd, &created) == nil && validFileStat(&created) && writeAll(fd, data) == nil && unix.Fsync(fd) == nil
	closeErr := unix.Close(fd)
	if !ok || closeErr != nil || unix.Fsync(d.fd) != nil {
		d.removeIfSame(name, created)
		return ErrPrivate
	}
	return nil
}

// Replace atomically makes data the contents of name. Existing targets must
// already be private regular files; unsafe files are never repaired in place.
func (d *Directory) Replace(name string, data []byte) error {
	if !validName(name) || len(data) > maxPayload {
		return ErrPrivate
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closed {
		return ErrClosed
	}
	if !d.validExisting(name) {
		return ErrPrivate
	}
	temporary, fd, err := d.createTemporary()
	if err != nil {
		return ErrPrivate
	}
	var created unix.Stat_t
	ok := unix.Fstat(fd, &created) == nil && validFileStat(&created) && writeAll(fd, data) == nil && unix.Fsync(fd) == nil
	closeErr := unix.Close(fd)
	if !ok || closeErr != nil {
		d.removeIfSame(temporary, created)
		return ErrPrivate
	}
	if unix.Renameat(d.fd, temporary, d.fd, name) != nil || unix.Fsync(d.fd) != nil {
		d.removeIfSame(temporary, created)
		return ErrPrivate
	}
	return nil
}

// Close invalidates the retained descriptor. It is safe to call repeatedly.
func (d *Directory) Close() error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closed {
		return nil
	}
	d.closed = true
	if err := d.dir.Close(); err != nil {
		return ErrPrivate
	}
	return nil
}

func (d *Directory) validExisting(name string) bool {
	var stat unix.Stat_t
	err := unix.Fstatat(d.fd, name, &stat, unix.AT_SYMLINK_NOFOLLOW)
	if errors.Is(err, unix.ENOENT) {
		return true
	}
	return err == nil && validFileStat(&stat)
}

func (d *Directory) createTemporary() (string, int, error) {
	for range 8 {
		var random [16]byte
		if _, err := rand.Read(random[:]); err != nil {
			return "", -1, ErrPrivate
		}
		name := ".anvil-connect-tmp-" + hex.EncodeToString(random[:])
		fd, err := unix.Openat(d.fd, name, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0600)
		if errors.Is(err, unix.EEXIST) {
			continue
		}
		if err != nil {
			return "", -1, ErrPrivate
		}
		return name, fd, nil
	}
	return "", -1, ErrPrivate
}

func (d *Directory) removeIfSame(name string, want unix.Stat_t) {
	if want.Ino == 0 {
		return
	}
	var found unix.Stat_t
	if unix.Fstatat(d.fd, name, &found, unix.AT_SYMLINK_NOFOLLOW) != nil || !validFileStat(&found) || found.Dev != want.Dev || found.Ino != want.Ino {
		return
	}
	_ = unix.Unlinkat(d.fd, name, 0)
	_ = unix.Fsync(d.fd)
}

func validFileFD(fd int) bool {
	var stat unix.Stat_t
	return unix.Fstat(fd, &stat) == nil && validFileStat(&stat)
}

func validFileStat(stat *unix.Stat_t) bool {
	return stat != nil && stat.Mode&unix.S_IFMT == unix.S_IFREG && stat.Mode&07777 == 0600 && stat.Nlink == 1 && stat.Uid == uint32(os.Geteuid()) && stat.Size >= 0 && stat.Size <= maxPayload
}

func writeAll(fd int, data []byte) error {
	for len(data) > 0 {
		n, err := unix.Write(fd, data)
		if err != nil || n <= 0 {
			return ErrPrivate
		}
		data = data[n:]
	}
	return nil
}

func validName(name string) bool {
	if len(name) < 1 || len(name) > 128 || filepath.Base(name) != name || name == "." || name == ".." {
		return false
	}
	for _, char := range name {
		if !(char >= 'a' && char <= 'z' || char >= 'A' && char <= 'Z' || char >= '0' && char <= '9' || char == '.' || char == '_' || char == '-') {
			return false
		}
	}
	return true
}
