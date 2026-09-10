// Package runtime assembles the reviewed Connect components into owned native
// processes. Public declarations contain references, never credential values.
package runtime

import (
	"errors"
	"io"
	"net/url"
	"path/filepath"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

var ErrConfiguration = errors.New("invalid native runtime declaration")

type OIDC struct {
	Issuer          string `json:"issuer"`
	ClientID        string `json:"client_id"`
	ClientSecretEnv string `json:"client_secret_env"`
}

type GatewayConfig struct {
	Schema                        string         `json:"schema"`
	Gateway                       config.Gateway `json:"gateway"`
	ControlHost                   string         `json:"control_host"`
	TunnelHost                    string         `json:"tunnel_host"`
	StateDirectory                string         `json:"state_directory"`
	TunnelBinary                  string         `json:"tunnel_binary"`
	TunnelListen                  string         `json:"tunnel_listen"`
	BrowserSessionLifetimeSeconds *int           `json:"browser_session_lifetime_seconds,omitempty"`
	OIDC                          OIDC           `json:"oidc"`
}

type ConnectorResource struct {
	Envelope       config.Envelope `json:"envelope"`
	ReverseAddress string          `json:"reverse_address"`
}

type ConnectorConfig struct {
	Schema          string              `json:"schema"`
	ID              string              `json:"id"`
	ControlHost     string              `json:"control_host"`
	TunnelHost      string              `json:"tunnel_host"`
	StateDirectory  string              `json:"state_directory"`
	TunnelBinary    string              `json:"tunnel_binary"`
	PublicTrustFile string              `json:"public_trust_file"`
	HTTPProxyURL    string              `json:"http_proxy_url"`
	Resources       []ConnectorResource `json:"resources"`
}

// ClientConfig remains an alias for the original Linux runtime API. Portable
// clients use clientconfig directly so they do not import this Linux runtime.
type ClientConfig = clientconfig.Config

func absolutePath(value string) bool {
	return filepath.IsAbs(value) && filepath.Clean(value) == value && value != "/" && !strings.ContainsAny(value, "\x00\r\n\t")
}

func (c GatewayConfig) Validate() error {
	if c.Schema != "anvil-connect.gateway-runtime/v1" || c.Gateway.Validate() != nil || !config.ValidHost(c.ControlHost) || !config.ValidHost(c.TunnelHost) || c.ControlHost == c.TunnelHost || !absolutePath(c.StateDirectory) || !absolutePath(c.TunnelBinary) || !config.LoopbackAddress(c.TunnelListen) || c.TunnelListen == c.Gateway.Listen || (c.BrowserSessionLifetimeSeconds != nil && (*c.BrowserSessionLifetimeSeconds < 60 || *c.BrowserSessionLifetimeSeconds > int(session.MaximumSessionLifetime/time.Second))) {
		return ErrConfiguration
	}
	browser := false
	for _, r := range c.Gateway.Resources {
		if r.Rule.Host == c.ControlHost || r.Rule.Host == c.TunnelHost || r.TunnelAddress == c.TunnelListen {
			return ErrConfiguration
		}
		browser = browser || r.Rule.Access == "browser"
	}
	if !browser {
		if c.OIDC != (OIDC{}) || c.BrowserSessionLifetimeSeconds != nil {
			return ErrConfiguration
		}
		return nil
	}
	u, err := url.Parse(c.OIDC.Issuer)
	if err != nil || u.Scheme != "https" || !config.ValidHost(u.Host) || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || strings.ContainsAny(c.OIDC.Issuer, "?#\\ ") || len(c.OIDC.ClientID) < 1 || len(c.OIDC.ClientID) > 128 || strings.ContainsAny(c.OIDC.ClientID, "\r\n\t ") || !config.ValidEnv(c.OIDC.ClientSecretEnv) {
		return ErrConfiguration
	}
	if u.Host == c.ControlHost || u.Host == c.TunnelHost {
		return ErrConfiguration
	}
	for _, r := range c.Gateway.Resources {
		if u.Host == r.Rule.Host {
			return ErrConfiguration
		}
	}
	return nil
}

// BrowserSessionLifetime resolves the optional public declaration to the
// established session authority default. Validate must succeed before startup.
func (c GatewayConfig) BrowserSessionLifetime() time.Duration {
	if c.BrowserSessionLifetimeSeconds == nil {
		return session.DefaultSessionLifetime
	}
	return time.Duration(*c.BrowserSessionLifetimeSeconds) * time.Second
}

func (c ConnectorConfig) Validate() error {
	if c.Schema != "anvil-connect.connector-runtime/v1" || !config.ValidID(c.ID) || !config.ValidHost(c.ControlHost) || !config.ValidHost(c.TunnelHost) || c.ControlHost == c.TunnelHost || !absolutePath(c.StateDirectory) || !absolutePath(c.TunnelBinary) || !absolutePath(c.PublicTrustFile) {
		return ErrConfiguration
	}
	local := config.Connector{Schema: "anvil-connect.connector/v1", ID: c.ID}
	reverse := map[string]bool{}
	for _, r := range c.Resources {
		if !config.LoopbackAddress(r.ReverseAddress) || reverse[r.ReverseAddress] || r.Envelope.Rule.Host == c.ControlHost || r.Envelope.Rule.Host == c.TunnelHost {
			return ErrConfiguration
		}
		reverse[r.ReverseAddress] = true
		local.Resources = append(local.Resources, r.Envelope)
	}
	if local.Validate() != nil {
		return ErrConfiguration
	}
	if c.HTTPProxyURL != "" {
		u, err := url.Parse(c.HTTPProxyURL)
		if err != nil || u.Scheme != "http" || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || strings.ContainsAny(c.HTTPProxyURL, "?#\\ \r\n\t") {
			return ErrConfiguration
		}
	}
	return nil
}

func ReadGateway(reader io.Reader) (GatewayConfig, error) {
	var c GatewayConfig
	if config.Decode(reader, &c) != nil || c.Validate() != nil {
		return GatewayConfig{}, ErrConfiguration
	}
	return c, nil
}

func ReadConnector(reader io.Reader) (ConnectorConfig, error) {
	var c ConnectorConfig
	if config.Decode(reader, &c) != nil || c.Validate() != nil {
		return ConnectorConfig{}, ErrConfiguration
	}
	return c, nil
}

func ReadClient(reader io.Reader) (ClientConfig, error) {
	c, err := clientconfig.Read(reader)
	if err != nil {
		return ClientConfig{}, ErrConfiguration
	}
	return c, nil
}
