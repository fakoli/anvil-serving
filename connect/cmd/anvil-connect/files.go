package main

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"golang.org/x/sys/unix"
)

var errFile = errors.New("file unavailable")

func absolute(path string) bool {
	return filepath.IsAbs(path) && filepath.Clean(path) == path && path != "/" && !strings.ContainsAny(path, "\x00\r\n\t")
}

// Configs may be public, but cannot be substituted with writable shared files,
// FIFOs or symlinks. NONBLOCK prevents an invalid FIFO from hanging before fstat.
func readDeclaration(path string) ([]byte, error) {
	if !absolute(path) {
		return nil, errFile
	}
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, errFile
	}
	f := os.NewFile(uintptr(fd), "declaration")
	defer f.Close()
	var stat unix.Stat_t
	if unix.Fstat(fd, &stat) != nil || stat.Mode&unix.S_IFMT != unix.S_IFREG || stat.Mode&0022 != 0 || (stat.Uid != 0 && stat.Uid != uint32(os.Geteuid())) || stat.Size > 1024*1024 {
		return nil, errFile
	}
	data, err := io.ReadAll(io.LimitReader(f, 1024*1024+1))
	if err != nil || len(data) > 1024*1024 {
		return nil, errFile
	}
	return data, nil
}

func pinPrivatePath(path string) (*privatefiles.PinnedPath, error) {
	if !absolute(path) {
		return nil, errFile
	}
	directory, err := privatefiles.Open(filepath.Dir(path))
	if err != nil {
		return nil, errFile
	}
	defer directory.Close()
	return directory.PinPath(filepath.Base(path))
}

func readPrivate(path string) ([]byte, error) {
	if !absolute(path) {
		return nil, errFile
	}
	directory, err := privatefiles.OpenExisting(filepath.Dir(path))
	if err != nil {
		return nil, errFile
	}
	defer directory.Close()
	return directory.Read(filepath.Base(path), 1024*1024)
}

// Reserve an exclusive output before any credential-issuing RPC. Retain both
// its descriptor and directory binding until the write and fsync complete.
type privateOutput struct {
	file     *os.File
	path     *privatefiles.PinnedPath
	stat     unix.Stat_t
	complete bool
}

func reserveOutput(path string) (*privateOutput, error) {
	pin, err := pinPrivatePath(path)
	if err != nil {
		return nil, errFile
	}
	fd, err := unix.Open(pin.Path(), unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0600)
	if err != nil {
		pin.Close()
		return nil, errFile
	}
	f := os.NewFile(uintptr(fd), "private-output")
	output := &privateOutput{file: f, path: pin}
	if unix.Fstat(fd, &output.stat) != nil || output.stat.Mode&07777 != 0600 || output.stat.Nlink != 1 || output.stat.Uid != uint32(os.Geteuid()) {
		output.Close()
		return nil, errFile
	}
	return output, nil
}

func (o *privateOutput) Write(data []byte) error {
	if o.complete || len(data) > 1024*1024 {
		return errFile
	}
	if n, err := o.file.Write(data); err != nil || n != len(data) {
		return errFile
	}
	if o.file.Sync() != nil {
		return errFile
	}
	var current unix.Stat_t
	if unix.Lstat(o.path.Path(), &current) != nil || current.Dev != o.stat.Dev || current.Ino != o.stat.Ino || current.Mode&07777 != 0600 || current.Nlink != 1 {
		return errFile
	}
	parent, err := os.Open(filepath.Dir(o.path.Path()))
	if err != nil {
		return errFile
	}
	err = parent.Sync()
	_ = parent.Close()
	if err != nil {
		return errFile
	}
	o.complete = true
	return nil
}

func (o *privateOutput) Close() {
	if !o.complete {
		var current unix.Stat_t
		if unix.Lstat(o.path.Path(), &current) == nil && current.Dev == o.stat.Dev && current.Ino == o.stat.Ino {
			_ = unix.Unlink(o.path.Path())
		}
	}
	_ = o.file.Close()
	_ = o.path.Close()
}
