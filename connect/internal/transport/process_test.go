package transport

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

func TestManagedInputsKeepVerifiedFileAndDirectoryInodes(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "owned")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "headers")
	if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	var in inputs
	defer in.close()
	if _, err := in.file(path, true); err != nil {
		t.Fatal(err)
	}
	if _, err := in.rotatingHeaders(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(dir, dir+"-old"); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	data, err := io.ReadAll(in.files[0])
	if err != nil || string(data) != "original" {
		t.Fatal("verified input followed replacement path")
	}
	// Open through the pinned directory descriptor just as the child rereads
	// headers. The replaced pathname cannot redirect it to the new directory.
	f, err := os.Open(filepath.Join("/proc/self/fd", strconv.Itoa(int(in.files[1].Fd())), "headers"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	data, err = io.ReadAll(f)
	if err != nil || string(data) != "original" {
		t.Fatal("rotating header directory changed after inspection")
	}
	if err := os.Chmod(dir, 0755); err != nil {
		t.Fatal(err)
	}
	if _, err := in.rotatingHeaders(path); err == nil {
		t.Fatal("shared rotating header directory accepted")
	}
}

func TestManagedProcessRefusesUnpinnedBinary(t *testing.T) {
	path := filepath.Join(t.TempDir(), "untrusted")
	if err := os.WriteFile(path, []byte("#!/bin/sh\nexit 0\n"), 0700); err != nil {
		t.Fatal(err)
	}
	if f, err := pinnedFile(path); err == nil {
		f.Close()
		t.Fatal("unreviewed executable accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if process, err := start(ctx, path, nil, nil, nil); err == nil {
		process.Close()
		t.Fatal("cancelled process started")
	}
}

func inputFile(path string, private bool) bool {
	f, err := openInput(path, private)
	if err != nil {
		return false
	}
	f.Close()
	return true
}

func TestManagedProcessInputFilePermissions(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "key")
	if err := os.WriteFile(path, []byte("synthetic material"), 0600); err != nil {
		t.Fatal(err)
	}
	if !inputFile(path, true) {
		t.Fatal("private owned file rejected")
	}
	alias := filepath.Join(dir, "alias")
	if err := os.Symlink(path, alias); err != nil {
		t.Fatal(err)
	}
	if inputFile(alias, true) {
		t.Fatal("symlink accepted")
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if inputFile(path, true) || !inputFile(path, false) {
		t.Fatal("private/public file distinction lost")
	}
	if process, err := StartServer(context.Background(), ServerOptions{Listen: "0.0.0.0:9443"}); err == nil {
		process.Close()
		t.Fatal("public private-transport listener accepted")
	}
	if process, err := StartClient(context.Background(), ClientOptions{ServerURL: "ws://connect.example.test"}); err == nil {
		process.Close()
		t.Fatal("plaintext connector admitted")
	}
}
