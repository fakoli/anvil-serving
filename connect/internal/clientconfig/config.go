// Package clientconfig validates the portable local forwarder declaration.
// It deliberately has no gateway, connector, process, or private-state
// dependency so a client can run on a separately provisioned workstation.
package clientconfig

import (
	"errors"
	"io"

	"github.com/fakoli/anvil-serving/connect/internal/config"
)

var ErrConfiguration = errors.New("invalid local client declaration")

// Config fixes one loopback listener, one declared API resource, and the two
// explicit environment references used by the local and remote credentials.
// It cannot express a gateway or connector role.
type Config struct {
	Schema       string      `json:"schema"`
	Rule         config.Rule `json:"rule"`
	Listen       string      `json:"listen"`
	LocalKeyEnv  string      `json:"local_key_env"`
	RemoteKeyEnv string      `json:"remote_key_env"`
}

func (c Config) Validate() error {
	if c.Schema != "anvil-connect.client-runtime/v1" || c.Rule.Validate() != nil || c.Rule.Access != "api" || !config.LoopbackAddress(c.Listen) || !config.ValidEnv(c.LocalKeyEnv) || !config.ValidEnv(c.RemoteKeyEnv) || c.LocalKeyEnv == c.RemoteKeyEnv {
		return ErrConfiguration
	}
	return nil
}

// Read accepts only the closed JSON grammar used for all Connect declarations.
func Read(reader io.Reader) (Config, error) {
	var c Config
	if config.Decode(reader, &c) != nil || c.Validate() != nil {
		return Config{}, ErrConfiguration
	}
	return c, nil
}
