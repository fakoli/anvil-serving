package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
	"golang.org/x/sys/unix"
)

func TestLoginUsesInstalledConfigAndExplicitOverride(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	directory := filepath.Join(home, ".config", "anvil-connect")
	if err := os.MkdirAll(directory, 0700); err != nil {
		t.Fatal(err)
	}
	declaration := clientconfig.Config{Schema: "anvil-connect.client-runtime/v1", Rule: portableClientRule(), Listen: "127.0.0.1:8787", LocalKeyEnv: "TEST_LOCAL_KEY", RemoteKeyEnv: "TEST_REMOTE_KEY"}
	raw, err := json.Marshal(declaration)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "client.json")
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"login"}, {"login", "--json"}, {"login", "--config", path}} {
		var out, diag bytes.Buffer
		// An invalid explicitly supplied local key proves the selected declaration
		// was read, and must fail before any device request or file fallback.
		code := run(context.Background(), args, &out, &diag, func(name string) (string, bool) {
			if name != "TEST_LOCAL_KEY" {
				t.Errorf("unexpected environment lookup: %s", name)
			}
			return "invalid-test-key", true
		})
		if code != 1 || !strings.Contains(diag.String(), "local key unavailable") || out.Len() != 0 {
			t.Fatalf("code=%d output=%q diagnostic=%q", code, out.String(), diag.String())
		}
	}
	var emptyOut, emptyDiag bytes.Buffer
	if code := run(context.Background(), []string{"login", "--config", ""}, &emptyOut, &emptyDiag, os.LookupEnv); code != 2 || !strings.Contains(emptyDiag.String(), "invalid command") {
		t.Fatal("empty explicit config silently selected defaults")
	}
	missing := filepath.Join(directory, "missing.json")
	selected, err := loginConfigPath(missing, home)
	if err != nil || selected != missing {
		t.Fatal("explicit config fell back")
	}
	for _, path := range []string{"relative.json", "/tmp/../client.json", "/bad\npath"} {
		if _, err := loginConfigPath(path, home); err == nil {
			t.Fatal("invalid config path accepted")
		}
	}
	var out, diag bytes.Buffer
	if code := run(context.Background(), []string{"login", "--config", missing}, &out, &diag, os.LookupEnv); code != 2 || !strings.Contains(diag.String(), "client setup unavailable") {
		t.Fatal("missing explicit setup did not fail")
	}
}

func TestLoginLocalSecretScopeAndOverride(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	key, err := client.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "local-key"), []byte(key+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	c := clientconfig.Config{LocalKeyEnv: "TEST_LOCAL_KEY", RemoteKeyEnv: "TEST_REMOTE_KEY"}
	path := filepath.Join(directory, "client.json")
	lookup, err := loginSecrets(path, c, func(name string) (string, bool) {
		if name != c.LocalKeyEnv {
			t.Fatal("unexpected environment read")
		}
		return "", false
	})
	if err != nil {
		t.Fatal(err)
	}
	if got, ok := lookup(c.LocalKeyEnv); !ok || got != key {
		t.Fatal("installed local key not loaded")
	}
	if got, ok := lookup(c.RemoteKeyEnv); ok || got != "" {
		t.Fatal("secret exposed under another name")
	}
	for _, value := range []string{"", "invalid-test-key"} {
		if _, err := loginSecrets(path, c, func(string) (string, bool) { return value, true }); err == nil {
			t.Fatal("invalid explicit key fell back to disk")
		}
	}
	other, err := client.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	lookup, err = loginSecrets(path, c, func(string) (string, bool) { return other, true })
	if err != nil {
		t.Fatal(err)
	}
	if got, ok := lookup(c.LocalKeyEnv); !ok || got != other {
		t.Fatal("explicit local key ignored")
	}
}

func TestLoginRefusesUnsafeKeyStorageWithoutChangingIt(t *testing.T) {
	for _, kind := range []string{"missing", "symlink", "parent-symlink", "hardlink", "directory", "fifo", "public-file", "public-parent", "malformed", "oversize", "extra-newline"} {
		t.Run(kind, func(t *testing.T) {
			root := t.TempDir()
			if err := os.Chmod(root, 0700); err != nil {
				t.Fatal(err)
			}
			directory := filepath.Join(root, "private")
			if err := os.Mkdir(directory, 0700); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(directory, "local-key")
			key, err := client.GenerateKey()
			if err != nil {
				t.Fatal(err)
			}
			data := []byte(key + "\n")
			switch kind {
			case "missing":
			case "directory":
				err = os.Mkdir(path, 0700)
			case "fifo":
				err = unix.Mkfifo(path, 0600)
			default:
				if kind == "malformed" {
					data = []byte(strings.Repeat("!", 48))
				}
				if kind == "oversize" {
					data = []byte(strings.Repeat("!", 10000))
				}
				if kind == "extra-newline" {
					data = append(data, '\n')
				}
				err = os.WriteFile(path, data, 0600)
			}
			if err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "symlink":
				target := filepath.Join(directory, "target")
				if err = os.Rename(path, target); err == nil {
					err = os.Symlink(target, path)
				}
			case "parent-symlink":
				link := filepath.Join(root, "alias")
				err = os.Symlink(directory, link)
				path = filepath.Join(link, "local-key")
			case "hardlink":
				err = os.Link(path, filepath.Join(directory, "alias"))
			case "public-file":
				err = os.Chmod(path, 0644)
			case "public-parent":
				err = os.Chmod(directory, 0755)
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := loginFileKey(path); err == nil {
				t.Fatal("unsafe storage accepted")
			}
			if kind == "missing" {
				if _, err := os.Lstat(path); !os.IsNotExist(err) {
					t.Fatal("login created a key")
				}
			}
			if kind == "malformed" || kind == "oversize" || kind == "extra-newline" || kind == "public-file" {
				after, err := os.ReadFile(path)
				if err != nil || !bytes.Equal(after, data) {
					t.Fatal("login modified the key")
				}
			}
		})
	}
}
