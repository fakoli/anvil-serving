//go:build darwin

package main

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"golang.org/x/sys/unix"
)

var errFile = errors.New("file unavailable")

func absolute(path string) bool {
	return filepath.IsAbs(path) && filepath.Clean(path) == path && path != "/" && !strings.ContainsAny(path, "\x00\r\n\t")
}

// Configs may be public, but cannot be substituted with writable shared files,
// FIFOs, or symlinks. NONBLOCK prevents an invalid FIFO from hanging before
// fstat. The client declaration contains only resource policy and env refs.
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

// privateOutput keeps an opened owner-only directory root through the whole
// credential write. Its output name is an exclusive single path component, so
// neither link following nor a directory replacement can retarget the secret.
type privateOutput struct {
	file     *os.File
	root     *os.Root
	name     string
	info     os.FileInfo
	complete bool
}

func privateRoot(path string) (*os.Root, error) {
	before, err := os.Lstat(path)
	if os.IsNotExist(err) {
		// Match the Linux key path: create only the final private directory,
		// then reject it unless the resulting directory is exclusively owned.
		if os.MkdirAll(path, 0700) != nil {
			return nil, errFile
		}
		before, err = os.Lstat(path)
	}
	if err != nil || !before.IsDir() || before.Mode().Perm() != 0700 || before.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		return nil, errFile
	}
	stat, ok := before.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return nil, errFile
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		return nil, errFile
	}
	after, err := root.Stat(".")
	if err != nil || !os.SameFile(before, after) {
		_ = root.Close()
		return nil, errFile
	}
	return root, nil
}

func reserveOutput(path string) (*privateOutput, error) {
	if !absolute(path) {
		return nil, errFile
	}
	parent, name := filepath.Dir(path), filepath.Base(path)
	if name == "." || name == string(filepath.Separator) {
		return nil, errFile
	}
	root, err := privateRoot(parent)
	if err != nil {
		return nil, errFile
	}
	f, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL|unix.O_NOFOLLOW, 0600)
	if err != nil {
		_ = root.Close()
		return nil, errFile
	}
	info, err := f.Stat()
	if err != nil {
		_ = f.Close()
		_ = root.Close()
		return nil, errFile
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || stat.Uid != uint32(os.Geteuid()) || stat.Nlink != 1 {
		(&privateOutput{file: f, root: root, name: name, info: info}).Close()
		return nil, errFile
	}
	return &privateOutput{file: f, root: root, name: name, info: info}, nil
}

func (o *privateOutput) Write(data []byte) error {
	if o == nil || o.complete || len(data) > 1024*1024 {
		return errFile
	}
	if n, err := o.file.Write(data); err != nil || n != len(data) || o.file.Sync() != nil {
		return errFile
	}
	current, err := o.root.Stat(o.name)
	if err != nil {
		return errFile
	}
	stat, ok := current.Sys().(*syscall.Stat_t)
	if !ok || !os.SameFile(o.info, current) || !current.Mode().IsRegular() || current.Mode().Perm() != 0600 || stat.Uid != uint32(os.Geteuid()) || stat.Nlink != 1 {
		return errFile
	}
	parent, err := o.root.OpenFile(".", os.O_RDONLY, 0)
	if err != nil || parent.Sync() != nil {
		if parent != nil {
			_ = parent.Close()
		}
		return errFile
	}
	_ = parent.Close()
	o.complete = true
	return nil
}

func (o *privateOutput) Close() {
	if o == nil {
		return
	}
	if !o.complete && o.root != nil {
		if current, err := o.root.Stat(o.name); err == nil && os.SameFile(o.info, current) {
			_ = o.root.Remove(o.name)
		}
	}
	if o.file != nil {
		_ = o.file.Close()
	}
	if o.root != nil {
		_ = o.root.Close()
	}
}
