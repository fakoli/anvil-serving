//go:build linux

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	connectruntime "github.com/fakoli/anvil-serving/connect/internal/runtime"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"golang.org/x/sys/unix"
)

func TestNativeBackupRestoreRequiresIndependentDigestAndPrivateOutput(t *testing.T) {
	parent := filepath.Join(t.TempDir(), "private")
	if err := os.Mkdir(parent, 0700); err != nil {
		t.Fatal(err)
	}
	c := connectruntime.GatewayConfig{Schema: "anvil-connect.gateway-runtime/v1", Gateway: config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:18100", MaxConcurrent: 2, Resources: []config.Resource{{Rule: cliRule(), Connector: "origin", TunnelAddress: "127.0.0.1:18101"}}}, ControlHost: "control.example.test", TunnelHost: "tunnel.example.test", StateDirectory: filepath.Join(parent, "source"), TunnelBinary: "/missing/wstunnel", TunnelListen: "127.0.0.1:18102"}
	if err := connectruntime.InitializeGateway(c); err != nil {
		t.Fatal(err)
	}
	s, err := store.Open(filepath.Join(c.StateDirectory, "authority"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Update(func(tx *store.Tx) error {
		return tx.Put("principals", "owner", map[string]any{"disabled": false, "generation": 1, "private_sentinel": "backup-contents-never-stdout"})
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	file := jsonFile(t, "gateway.json", c)
	output := filepath.Join(parent, "backup.json")
	var stdout, stderr bytes.Buffer
	args := []string{"backup", "--config", file, "--output", output}
	if code := run(context.Background(), args, &stdout, &stderr, noSecrets(t)); code != 0 {
		t.Fatal("backup failed", code)
	}
	if strings.Contains(stdout.String(), "backup-contents-never-stdout") || strings.Contains(stdout.String(), "PRIVATE KEY") || stderr.Len() != 0 {
		t.Fatal("private backup material leaked")
	}
	var result map[string]string
	if json.Unmarshal(stdout.Bytes(), &result) != nil || len(result["sha256"]) != 64 {
		t.Fatal("backup digest absent")
	}
	original, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	if run(context.Background(), args, &stdout, &stderr, noSecrets(t)) == 0 {
		t.Fatal("existing backup replaced")
	}
	current, _ := os.ReadFile(output)
	if !bytes.Equal(original, current) {
		t.Fatal("backup changed on repeated command")
	}
	c.StateDirectory = filepath.Join(parent, "restored")
	file = jsonFile(t, "restore.json", c)
	args = []string{"restore", "--config", file, "--input", output}
	if run(context.Background(), args, &stdout, &stderr, noSecrets(t)) == 0 {
		t.Fatal("restore accepted missing trusted digest")
	}
	if _, err := os.Lstat(c.StateDirectory); !os.IsNotExist(err) {
		t.Fatal("invalid digest touched target")
	}
	args = append(args, "--sha256", result["sha256"])
	stdout.Reset()
	stderr.Reset()
	if run(context.Background(), args, &stdout, &stderr, noSecrets(t)) != 0 {
		t.Fatal("restore failed")
	}
	if !strings.Contains(stdout.String(), `"grants":"disabled"`) || stderr.Len() != 0 {
		t.Fatal("recovery semantics absent")
	}
}

func TestPrivateInputFIFORejectedWithoutOpeningPipe(t *testing.T) {
	parent := filepath.Join(t.TempDir(), "private")
	if err := os.Mkdir(parent, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(parent, "input")
	if err := unix.Mkfifo(path, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readPrivate(path); err == nil {
		t.Fatal("FIFO accepted as private input")
	}
}

func TestPrivateReadDoesNotCreateMissingParent(t *testing.T) {
	parent := filepath.Join(t.TempDir(), "missing")
	if _, err := readPrivate(filepath.Join(parent, "archive")); err == nil {
		t.Fatal("missing input accepted")
	}
	if _, err := os.Lstat(parent); !os.IsNotExist(err) {
		t.Fatal("private read created missing directory")
	}
}

func TestArtifactPreflightFailsBeforeStateOrSecretLookup(t *testing.T) {
	c := connectruntime.GatewayConfig{Schema: "anvil-connect.gateway-runtime/v1", Gateway: config.Gateway{Schema: "anvil-connect.gateway/v1", Listen: "127.0.0.1:18100", MaxConcurrent: 2, Resources: []config.Resource{{Rule: cliRule(), Connector: "origin", TunnelAddress: "127.0.0.1:18101"}}}, ControlHost: "control.example.test", TunnelHost: "tunnel.example.test", StateDirectory: filepath.Join(t.TempDir(), "absent"), TunnelBinary: "/missing/wstunnel", TunnelListen: "127.0.0.1:18102"}
	for _, binary := range []string{c.TunnelBinary, os.Getenv("ANVIL_CONNECT_WSTUNNEL")} {
		if binary == "" {
			continue
		}
		c.TunnelBinary = binary
		var stdout, stderr bytes.Buffer
		code := run(context.Background(), []string{"preflight", "--mode", "gateway", "--config", jsonFile(t, "config.json", c)}, &stdout, &stderr, noSecrets(t))
		if (binary == "/missing/wstunnel" && code == 0) || (binary != "/missing/wstunnel" && code != 0) {
			t.Fatal("artifact preflight result mismatch")
		}
		if _, err := os.Lstat(c.StateDirectory); !os.IsNotExist(err) {
			t.Fatal("artifact preflight created private state")
		}
	}
}
