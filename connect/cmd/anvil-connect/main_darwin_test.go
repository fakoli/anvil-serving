//go:build darwin

package main

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestDarwinKeyOutputIsPrivateExclusiveAndNeverPrinted(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(directory, "client-key")
	var stdout, stderr bytes.Buffer
	invoke := func(args ...string) int { return run(context.Background(), args, &stdout, &stderr, os.LookupEnv) }
	if got := invoke("keygen", "--output", output); got != 0 {
		t.Fatalf("keygen failed: %d %s", got, stderr.String())
	}
	key, err := os.ReadFile(output)
	if err != nil || len(key) < 32 {
		t.Fatal("key missing")
	}
	info, err := os.Stat(output)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("key permissions")
	}
	if bytes.Contains(stdout.Bytes(), bytes.TrimSpace(key)) || bytes.Contains(stderr.Bytes(), bytes.TrimSpace(key)) {
		t.Fatal("secret printed")
	}
	if got := invoke("keygen", "--output", output); got == 0 {
		t.Fatal("existing key overwritten")
	}
	after, _ := os.ReadFile(output)
	if !bytes.Equal(key, after) {
		t.Fatal("existing key changed")
	}
	link := filepath.Join(directory, "link")
	if err := os.Symlink(output, link); err != nil {
		t.Fatal(err)
	}
	if got := invoke("keygen", "--output", link); got == 0 {
		t.Fatal("symlink accepted")
	}
	if got := invoke("gateway", "--config", "/missing.json"); got != 2 {
		t.Fatal("unsupported server role accepted")
	}
	if got := invoke("validate", "--mode", "connector", "--config", "/missing.json"); got != 2 {
		t.Fatal("unsupported role accepted")
	}
}
