package main

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
	"golang.org/x/sys/unix"
)

var errLocalKey = errors.New("local client key unavailable")

// loginKeyLocation contains only the selected credential reference, never its value.
type loginKeyLocation struct {
	file string
	env  string
}

func loginConfigPath(file, home string) (string, error) {
	if file != "" {
		if !absolute(file) {
			return "", errFile
		}
		return file, nil
	}
	if !absolute(home) {
		return "", errFile
	}
	return filepath.Join(home, ".config", "anvil-connect", "client.json"), nil
}

// loginSecrets consults only the declared local environment reference, then
// the fixed local-key sibling of the selected configuration. It never loads
// shell files, unrelated environment files, or a persisted remote credential.
func loginSecrets(file string, c clientconfig.Config, lookup func(string) (string, bool)) (func(string) (string, bool), loginKeyLocation, error) {
	if lookup == nil || !absolute(file) {
		return nil, loginKeyLocation{}, errLocalKey
	}
	location := loginKeyLocation{env: c.LocalKeyEnv}
	key, present := lookup(c.LocalKeyEnv)
	if !present {
		location = loginKeyLocation{file: filepath.Join(filepath.Dir(file), "local-key")}
		var err error
		key, err = loginFileKey(location.file)
		if err != nil {
			return nil, loginKeyLocation{}, errLocalKey
		}
	}
	if !client.ValidLocalKey(key) {
		return nil, loginKeyLocation{}, errLocalKey
	}
	return func(name string) (string, bool) {
		if name == c.LocalKeyEnv {
			return key, true
		}
		return "", false
	}, location, nil
}

// The pinned directory and no-follow file descriptor retain the private-file
// boundary on Linux and macOS. Installation owns initial key creation; login
// never repairs, replaces, or generates credentials.
func loginFileKey(path string) (string, error) {
	parent := filepath.Dir(path)
	fd, err := unix.Open(parent, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return "", errLocalKey
	}
	defer unix.Close(fd)
	var directory unix.Stat_t
	if unix.Fstat(fd, &directory) != nil || directory.Mode&07777 != 0700 || directory.Uid != uint32(os.Geteuid()) {
		return "", errLocalKey
	}
	keyFD, err := unix.Openat(fd, "local-key", unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)

	if err != nil {
		return "", errLocalKey
	}
	f := os.NewFile(uintptr(keyFD), "local-client-key")
	defer f.Close()
	var metadata unix.Stat_t
	if unix.Fstat(keyFD, &metadata) != nil || metadata.Mode&unix.S_IFMT != unix.S_IFREG || metadata.Mode&07777 != 0600 || metadata.Uid != uint32(os.Geteuid()) || metadata.Nlink != 1 || (metadata.Size != 48 && metadata.Size != 49) {
		return "", errLocalKey
	}
	data, err := io.ReadAll(io.LimitReader(f, 50))
	if err != nil || int64(len(data)) != metadata.Size {
		return "", errLocalKey
	}
	key := strings.TrimSuffix(string(data), "\n")
	if !client.ValidLocalKey(key) {
		return "", errLocalKey
	}
	return key, nil
}
