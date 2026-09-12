// Package runtime assembles the reviewed Connect components into owned native
// processes. Public declarations contain references, never credential values.
package runtime

import (
	"errors"
	"fmt"
	"io"
	"net/url"
	"path/filepath"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/ingresshttp"
	"github.com/fakoli/anvil-serving/connect/internal/session"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
	"github.com/fakoli/anvil-serving/connect/internal/tunnelgate"
)

var ErrConfiguration = errors.New("invalid native runtime declaration")

type OIDC struct {
	Issuer          string `json:"issuer"`
	ClientID        string `json:"client_id"`
	ClientSecretEnv string `json:"client_secret_env"`
}

type LocalTunnelListener struct {
	Listen          string `json:"listen"`
	ServerName      string `json:"server_name"`
	HTTPHost        string `json:"http_host"`
	CertificateFile string `json:"certificate_file"`
	PrivateKeyFile  string `json:"private_key_file"`
	TrustFile       string `json:"trust_file"`
}

type LocalTunnelEndpoint struct {
	Address    string `json:"address"`
	ServerName string `json:"server_name"`
	HTTPHost   string `json:"http_host"`
	TrustFile  string `json:"trust_file"`
}

type GatewayConfig struct {
	Schema                        string               `json:"schema"`
	Gateway                       config.Gateway       `json:"gateway"`
	ControlHost                   string               `json:"control_host"`
	TunnelHost                    string               `json:"tunnel_host"`
	StateDirectory                string               `json:"state_directory"`
	Ingress                       *ingresshttp.Policy  `json:"ingress,omitempty"`
	TunnelBinary                  string               `json:"tunnel_binary"`
	TunnelListen                  string               `json:"tunnel_listen"`
	BrowserSessionLifetimeSeconds *int                 `json:"browser_session_lifetime_seconds,omitempty"`
	OIDC                          OIDC                 `json:"oidc"`
	LocalTunnel                   *LocalTunnelListener `json:"local_tunnel,omitempty"`
}

type ConnectorResource struct {
	Envelope       config.Envelope `json:"envelope"`
	ReverseAddress string          `json:"reverse_address"`
}

type ConnectorConfig struct {
	Schema          string               `json:"schema"`
	ID              string               `json:"id"`
	ControlHost     string               `json:"control_host"`
	TunnelHost      string               `json:"tunnel_host"`
	StateDirectory  string               `json:"state_directory"`
	TunnelBinary    string               `json:"tunnel_binary"`
	PublicTrustFile string               `json:"public_trust_file"`
	HTTPProxyURL    string               `json:"http_proxy_url"`
	Resources       []ConnectorResource  `json:"resources"`
	LocalTunnel     *LocalTunnelEndpoint `json:"local_tunnel,omitempty"`
}

func localTunnelError(reason string) error {
	return fmt.Errorf("%w: local_tunnel: %s", ErrConfiguration, reason)
}

func validateLocalTunnel(address, serverName, httpHost, state string, files ...string) error {
	if !config.LoopbackAddress(address) {
		return localTunnelError("must be a 127.0.0.1 TCP address")
	}
	if !config.ValidHost(serverName) || !config.ValidHost(httpHost) {
		return localTunnelError("must be a lower-case DNS host")
	}
	seen := map[string]bool{}
	for _, file := range files {
		if !absolutePath(file) {
			return localTunnelError("must be a clean absolute path")
		}
		if seen[file] || file == state || strings.HasPrefix(file, state+"/") {
			return localTunnelError("file references must be distinct and outside runtime state")
		}
		seen[file] = true
	}
	return nil
}

func localIdentityCollision(serverName, httpHost string, hosts ...string) bool {
	for _, host := range append(hosts, admin.Host, transport.GatewayPeer, tunnelgate.BackendPeer, tunnelgate.GatePeer) {
		if serverName == host || httpHost == host {
			return true
		}
	}
	return false
}

// ClientConfig remains an alias for the original Linux runtime API. Portable
// clients use clientconfig directly so they do not import this Linux runtime.
type ClientConfig = clientconfig.Config

func absolutePath(value string) bool {
	return filepath.IsAbs(value) && filepath.Clean(value) == value && value != "/" && !strings.ContainsAny(value, "\x00\r\n\t")
}

func (c GatewayConfig) Validate() error {
	if c.Ingress != nil && (c.Ingress.Validate() != nil || !disjointDirectories(c.StateDirectory, c.Ingress.Directory)) {
		return ErrConfiguration
	}
	if c.Schema != "anvil-connect.gateway-runtime/v1" || c.Gateway.Validate() != nil || !config.ValidHost(c.ControlHost) || !config.ValidHost(c.TunnelHost) || c.ControlHost == c.TunnelHost || !absolutePath(c.StateDirectory) || !absolutePath(c.TunnelBinary) || !config.LoopbackAddress(c.TunnelListen) || c.TunnelListen == c.Gateway.Listen || (c.BrowserSessionLifetimeSeconds != nil && (*c.BrowserSessionLifetimeSeconds < 60 || *c.BrowserSessionLifetimeSeconds > int(session.MaximumSessionLifetime/time.Second))) {
		return ErrConfiguration
	}
	if l := c.LocalTunnel; l != nil {
		if err := validateLocalTunnel(l.Listen, l.ServerName, l.HTTPHost, c.StateDirectory, l.CertificateFile, l.PrivateKeyFile, l.TrustFile); err != nil {
			return err
		}
		hosts := []string{c.ControlHost, c.TunnelHost, strings.TrimPrefix(c.OIDC.Issuer, "https://")}
		for _, r := range c.Gateway.Resources {
			hosts = append(hosts, r.Rule.Host, transport.ConnectorPeer(r.Connector))
			if l.Listen == r.TunnelAddress {
				return localTunnelError("must not reuse an existing listener")
			}
		}
		if l.Listen == c.Gateway.Listen || l.Listen == c.TunnelListen {
			return localTunnelError("must not reuse an existing listener")
		}
		if localIdentityCollision(l.ServerName, l.HTTPHost, hosts...) {
			return localTunnelError("must not reuse an existing service identity")
		}
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

func disjointDirectories(first, second string) bool {
	return first != second && !strings.HasPrefix(first, second+"/") && !strings.HasPrefix(second, first+"/")
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
	if l := c.LocalTunnel; l != nil {
		if err := validateLocalTunnel(l.Address, l.ServerName, l.HTTPHost, c.StateDirectory, l.TrustFile); err != nil {
			return err
		}
		if l.TrustFile == c.PublicTrustFile {
			return localTunnelError("must not reuse an existing trust reference")
		}
		hosts := []string{c.ControlHost, c.TunnelHost, transport.ConnectorPeer(c.ID)}
		for _, r := range c.Resources {
			hosts = append(hosts, r.Envelope.Rule.Host)
			if l.Address == r.ReverseAddress || l.Address == r.Envelope.Listen || l.Address == strings.TrimSuffix(strings.TrimPrefix(r.Envelope.OriginURL, "http://"), "/") {
				return localTunnelError("must not reuse an existing listener")
			}
		}
		if localIdentityCollision(l.ServerName, l.HTTPHost, hosts...) {
			return localTunnelError("must not reuse an existing service identity")
		}
	}
	if c.HTTPProxyURL != "" {
		u, err := url.Parse(c.HTTPProxyURL)
		if err != nil || u.Scheme != "http" || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || strings.ContainsAny(c.HTTPProxyURL, "?#\\ \r\n\t") {
			return ErrConfiguration
		}
	}
	return nil
}

// ValidateGateway checks the binding when both role declarations are available.
// A standalone connector reader cannot discover another role's declaration.
// Trust-file contents and colocation require the later activation preflight.
func (c ConnectorConfig) ValidateGateway(g GatewayConfig) error {
	if err := g.Validate(); err != nil {
		return err
	}
	if err := c.Validate(); err != nil {
		return err
	}
	if c.ControlHost != g.ControlHost || c.TunnelHost != g.TunnelHost {
		return ErrConfiguration
	}
	if l := c.LocalTunnel; l != nil {
		if g.LocalTunnel == nil || l.Address != g.LocalTunnel.Listen || l.ServerName != g.LocalTunnel.ServerName || l.HTTPHost != g.LocalTunnel.HTTPHost {
			return localTunnelError("must match gateway local_tunnel")
		}
	}
	if l := g.LocalTunnel; l != nil {
		for _, file := range []string{l.CertificateFile, l.PrivateKeyFile, l.TrustFile} {
			if file == c.PublicTrustFile {
				return localTunnelError("must not reuse an existing trust reference")
			}
			if file == c.StateDirectory || strings.HasPrefix(file, c.StateDirectory+"/") {
				return localTunnelError("file references must be distinct and outside runtime state")
			}
		}
		if endpoint := c.LocalTunnel; endpoint != nil {
			if endpoint.TrustFile == l.CertificateFile || endpoint.TrustFile == l.PrivateKeyFile {
				return localTunnelError("must not reuse an existing trust reference")
			}
			if endpoint.TrustFile == g.StateDirectory || strings.HasPrefix(endpoint.TrustFile, g.StateDirectory+"/") {
				return localTunnelError("file references must be distinct and outside runtime state")
			}
		}
		if localIdentityCollision(l.ServerName, l.HTTPHost, transport.ConnectorPeer(c.ID)) {
			return localTunnelError("must not reuse an existing service identity")
		}
		for _, r := range c.Resources {
			if l.Listen == r.ReverseAddress || l.Listen == r.Envelope.Listen || l.Listen == strings.TrimSuffix(strings.TrimPrefix(r.Envelope.OriginURL, "http://"), "/") {
				return localTunnelError("must not reuse an existing listener")
			}
		}
	}
	return nil
}

func ReadGateway(reader io.Reader) (GatewayConfig, error) {
	var c GatewayConfig
	if config.Decode(reader, &c) != nil {
		return GatewayConfig{}, ErrConfiguration
	}
	if err := c.Validate(); err != nil {
		return GatewayConfig{}, err
	}
	return c, nil
}

func ReadConnector(reader io.Reader) (ConnectorConfig, error) {
	var c ConnectorConfig
	if config.Decode(reader, &c) != nil {
		return ConnectorConfig{}, ErrConfiguration
	}
	if err := c.Validate(); err != nil {
		return ConnectorConfig{}, err
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

// outerClient resolves one path once, before any resource client is created.
// Control clients deliberately continue to consume the public declaration.
func (c ConnectorConfig) outerClient() transport.ClientOptions {
	o := transport.ClientOptions{ViaGate: true, Binary: c.TunnelBinary, ServerURL: "wss://" + c.TunnelHost, TrustFile: c.PublicTrustFile, ProxyURL: c.HTTPProxyURL}
	if l := c.LocalTunnel; l != nil {
		o.Local = &transport.LocalBinding{Address: l.Address, ServerName: l.ServerName, Host: l.HTTPHost}
		o.ServerURL, o.TrustFile, o.ProxyURL = "wss://"+l.Address, l.TrustFile, ""
	}
	return o
}
