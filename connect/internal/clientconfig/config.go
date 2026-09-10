// Package clientconfig validates the portable local forwarder declaration.
// It deliberately has no gateway, connector, process, or private-state
// dependency so a client can run on a separately provisioned workstation.
package clientconfig

import (
	"errors"
	"io"
	"strings"

	"github.com/fakoli/anvil-serving/connect/internal/config"
)

var ErrConfiguration = errors.New("invalid local client declaration")

// Config fixes one loopback listener, one declared API resource, and the two
// explicit environment references used by the local and remote credentials.
// It cannot express a gateway or connector role.
type Config struct {
	Schema              string               `json:"schema"`
	Rule                config.Rule          `json:"rule"`
	Listen              string               `json:"listen"`
	LocalKeyEnv         string               `json:"local_key_env"`
	RemoteKeyEnv        string               `json:"remote_key_env"`
	DeviceAuthorization *DeviceAuthorization `json:"device_authorization,omitempty"`
}

// DeviceAuthorization is copied from one closed gateway binding. It permits
// the portable login command to contact only its declared browser approval
// endpoint; it cannot select hosts, resources, or methods at runtime.
type DeviceAuthorization struct {
	BrowserHost  string   `json:"browser_host"`
	ApprovalPath string   `json:"approval_path"`
	APIResource  string   `json:"api_resource"`
	Methods      []string `json:"methods"`
}

func (c Config) Validate() error {
	if c.Schema != "anvil-connect.client-runtime/v1" || c.Rule.Validate() != nil || c.Rule.Access != "api" || !config.LoopbackAddress(c.Listen) || !config.ValidEnv(c.LocalKeyEnv) || !config.ValidEnv(c.RemoteKeyEnv) || c.LocalKeyEnv == c.RemoteKeyEnv || (c.DeviceAuthorization != nil && !c.DeviceAuthorization.Validate(c.Rule)) {
		return ErrConfiguration
	}
	return nil
}

func (d DeviceAuthorization) Validate(rule config.Rule) bool {
	if !config.ValidHost(d.BrowserHost) || !config.CanonicalPath(d.ApprovalPath) || d.ApprovalPath == "/" || strings.HasSuffix(d.ApprovalPath, "/") || d.APIResource != rule.ID || len(d.Methods) < 1 || len(d.Methods) > len(rule.Methods) {
		return false
	}
	seen := map[string]bool{}
	for _, method := range d.Methods {
		if seen[method] || !config.ValidMethod(method) || !rule.Allows(rule.Host, rule.PathPrefix, method) {
			return false
		}
		seen[method] = true
	}
	return true
}

// Read accepts only the closed JSON grammar used for all Connect declarations.
func Read(reader io.Reader) (Config, error) {
	var c Config
	if config.Decode(reader, &c) != nil || c.Validate() != nil {
		return Config{}, ErrConfiguration
	}
	return c, nil
}
