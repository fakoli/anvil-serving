package clientconfig

import (
	"strings"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
)

func validConfig() Config {
	return Config{
		Schema: "anvil-connect.client-runtime/v1",
		Rule: config.Rule{
			ID: "router", Host: "router.example.test", PathPrefix: "/v1",
			Methods: []string{"GET", "POST"}, Access: "api", NativeAuth: "delegate-bearer",
			Limits: config.Limits{RequestBytes: 1024, Concurrent: 1, BufferBytes: 4096, IdleSeconds: 1, DurationSeconds: 3},
		},
		Listen: "127.0.0.1:18080", LocalKeyEnv: "ANVIL_CONNECT_LOCAL_KEY", RemoteKeyEnv: "ANVIL_CONNECT_REMOTE_KEY",
	}
}

func TestReadAcceptsOnlyFixedAPILocalClient(t *testing.T) {
	valid := validConfig()
	for name, mutate := range map[string]func(*Config){
		"browser-rule":      func(c *Config) { c.Rule.Access, c.Rule.NativeAuth = "browser", "none" },
		"shared-secret-ref": func(c *Config) { c.RemoteKeyEnv = c.LocalKeyEnv },
		"non-loopback":      func(c *Config) { c.Listen = "127.0.0.2:18080" },
	} {
		t.Run(name, func(t *testing.T) {
			candidate := valid
			candidate.Rule.Methods = append([]string(nil), valid.Rule.Methods...)
			mutate(&candidate)
			if candidate.Validate() == nil {
				t.Fatal("unsafe client declaration accepted")
			}
		})
	}
	if valid.Validate() != nil {
		t.Fatal("valid client declaration rejected")
	}
	if _, err := Read(strings.NewReader(`{"schema":"anvil-connect.client-runtime/v1","rule":{},"listen":"127.0.0.1:18080","local_key_env":"ANVIL_CONNECT_LOCAL_KEY","remote_key_env":"ANVIL_CONNECT_REMOTE_KEY","unknown":true}`)); err == nil {
		t.Fatal("open declaration grammar accepted")
	}
}
