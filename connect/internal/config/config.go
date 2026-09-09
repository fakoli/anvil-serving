// Package config defines the independently provisioned gateway policy and
// connector envelope. Neither accepts destinations or credentials from callers.
package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"path"
	"reflect"
	"regexp"
	"strconv"
	"strings"
)

const MaxConfigBytes = 1024 * 1024

var (
	identifier  = regexp.MustCompile(`^[a-z][a-z0-9-]{0,62}$`)
	environment = regexp.MustCompile(`^[A-Z][A-Z0-9_]{0,127}$`)
	label       = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
)

type Limits struct {
	RequestBytes    int64 `json:"request_bytes"`
	Concurrent      int   `json:"concurrent"`
	BufferBytes     int   `json:"buffer_bytes"`
	IdleSeconds     int   `json:"idle_seconds"`
	DurationSeconds int   `json:"duration_seconds"`
}

func (l Limits) Validate() error {
	if l.RequestBytes < 1 || l.RequestBytes > 64*1024*1024 || l.Concurrent < 1 || l.Concurrent > 256 ||
		l.BufferBytes < 4096 || l.BufferBytes > 256*1024 || l.IdleSeconds < 1 || l.IdleSeconds > 300 ||
		l.DurationSeconds < l.IdleSeconds || l.DurationSeconds > 86400 {
		return errors.New("resource limits missing or outside supported bounds")
	}
	return nil
}

// Rule is explicitly copied into both declarations by the operator renderer.
// It is never downloaded from the gateway to widen a running connector.
type Rule struct {
	ID         string   `json:"id"`
	Host       string   `json:"host"`
	PathPrefix string   `json:"path_prefix"`
	Methods    []string `json:"methods"`
	Access     string   `json:"access"`
	NativeAuth string   `json:"native_auth"`
	Limits     Limits   `json:"limits"`
}

type Resource struct {
	Rule          Rule   `json:"rule"`
	Connector     string `json:"connector"`
	TunnelAddress string `json:"tunnel_address"`
}

type Gateway struct {
	Schema        string     `json:"schema"`
	Listen        string     `json:"listen"`
	MaxConcurrent int        `json:"max_concurrent"`
	Resources     []Resource `json:"resources"`
}

type Envelope struct {
	Rule      Rule   `json:"rule"`
	Listen    string `json:"listen"`
	OriginURL string `json:"origin_url"`
	TokenEnv  string `json:"token_env,omitempty"`
}

type Connector struct {
	Schema    string     `json:"schema"`
	ID        string     `json:"id"`
	Resources []Envelope `json:"resources"`
}

func ValidID(value string) bool  { return identifier.MatchString(value) }
func ValidEnv(value string) bool { return environment.MatchString(value) }

func ValidHost(value string) bool {
	if len(value) > 253 || !strings.Contains(value, ".") || net.ParseIP(value) != nil {
		return false
	}
	for _, part := range strings.Split(value, ".") {
		if !label.MatchString(part) {
			return false
		}
	}
	return true
}

// CanonicalPath is deliberately conservative for v1: escapes, dot segments,
// duplicate separators and non-ASCII paths need an explicit later contract.
// Query strings are not paths and do not participate in resource selection.
func CanonicalPath(value string) bool {
	if value == "" || value[0] != '/' || strings.ContainsAny(value, `%\?#`) {
		return false
	}
	for _, c := range value {
		if c < 33 || c > 126 {
			return false
		}
	}
	clean := path.Clean(value)
	return clean == value || (value != "/" && strings.HasSuffix(value, "/") && clean+"/" == value)
}

func ValidMethod(method string) bool {
	switch method {
	case "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS":
		return true
	default:
		return false
	}
}

func (r Rule) Validate() error {
	if !ValidID(r.ID) || !ValidHost(r.Host) || !CanonicalPath(r.PathPrefix) || (r.PathPrefix != "/" && strings.HasSuffix(r.PathPrefix, "/")) {
		return errors.New("invalid resource identity, canonical host or path prefix")
	}
	if r.Access != "api" && r.Access != "browser" {
		return errors.New("unknown access profile")
	}
	if (r.Access == "api" && r.NativeAuth != "delegate-bearer") || (r.Access == "browser" && r.NativeAuth != "none" && r.NativeAuth != "passthrough") {
		return errors.New("unsupported access and native-auth combination")
	}
	if len(r.Methods) < 1 || len(r.Methods) > 7 {
		return errors.New("explicit methods required")
	}
	seen := map[string]bool{}
	for _, method := range r.Methods {
		if !ValidMethod(method) || seen[method] {
			return errors.New("unsupported or duplicate method")
		}
		seen[method] = true
	}
	return r.Limits.Validate()
}

func (r Rule) Allows(host, requestPath, method string) bool {
	if host != r.Host || !CanonicalPath(requestPath) || !(r.PathPrefix == "/" || requestPath == r.PathPrefix || strings.HasPrefix(requestPath, r.PathPrefix+"/")) {
		return false
	}
	for _, allowed := range r.Methods {
		if allowed == method {
			return true
		}
	}
	return false
}

func LoopbackAddress(address string) bool {
	host, port, err := net.SplitHostPort(address)
	if err != nil || host != "127.0.0.1" {
		return false
	}
	n, err := strconv.Atoi(port)
	return err == nil && n > 0 && n < 65536 && strconv.Itoa(n) == port
}

func (g Gateway) Validate() error {
	if g.Schema != "anvil-connect.gateway/v1" || !LoopbackAddress(g.Listen) || g.MaxConcurrent < 1 || g.MaxConcurrent > 512 || len(g.Resources) < 1 || len(g.Resources) > 64 {
		return errors.New("invalid gateway declaration")
	}
	ids, hosts, addresses := map[string]bool{}, map[string]bool{}, map[string]bool{g.Listen: true}
	for _, resource := range g.Resources {
		if err := resource.Rule.Validate(); err != nil {
			return err
		}
		if !ValidID(resource.Connector) || !LoopbackAddress(resource.TunnelAddress) || ids[resource.Rule.ID] || hosts[resource.Rule.Host] || addresses[resource.TunnelAddress] || resource.Rule.Limits.Concurrent > g.MaxConcurrent {
			return errors.New("duplicate or invalid gateway resource binding")
		}
		ids[resource.Rule.ID], hosts[resource.Rule.Host], addresses[resource.TunnelAddress] = true, true, true
	}
	return nil
}

func (c Connector) Validate() error {
	if c.Schema != "anvil-connect.connector/v1" || !ValidID(c.ID) || len(c.Resources) < 1 || len(c.Resources) > 64 {
		return errors.New("invalid connector declaration")
	}
	ids, hosts, addresses := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, resource := range c.Resources {
		if err := resource.Rule.Validate(); err != nil {
			return err
		}
		if !LoopbackAddress(resource.Listen) || ids[resource.Rule.ID] || hosts[resource.Rule.Host] || addresses[resource.Listen] {
			return errors.New("duplicate or invalid local resource binding")
		}
		if err := validateOrigin(resource.OriginURL); err != nil {
			return err
		}
		if resource.Rule.NativeAuth == "delegate-bearer" {
			if !ValidEnv(resource.TokenEnv) {
				return errors.New("delegation requires an environment secret reference")
			}
		} else if resource.TokenEnv != "" {
			return errors.New("secret reference not allowed for this native-auth mode")
		}
		ids[resource.Rule.ID], hosts[resource.Rule.Host], addresses[resource.Listen] = true, true, true
	}
	for _, resource := range c.Resources {
		origin, _ := url.Parse(resource.OriginURL) // validated above
		if addresses[origin.Host] {
			return errors.New("origin cannot target a connector listener")
		}
	}
	return nil
}

func validateOrigin(value string) error {
	u, err := url.Parse(value)
	if err != nil || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.RawPath != "" || (u.Path != "" && u.Path != "/") || u.Opaque != "" || strings.ContainsAny(value, "\\\r\n\t ") {
		return errors.New("origin must be a fixed authority URL without credentials, path, query or fragment")
	}
	if !LoopbackAddress(u.Host) || u.Scheme != "http" {
		// The first connector is colocated with the application. Non-loopback
		// TLS origins require a separate trust and DNS-rebinding contract.
		return errors.New("v1 origin requires explicit HTTP loopback address and port")
	}
	return nil
}

func ReadGateway(reader io.Reader) (Gateway, error) {
	var result Gateway
	if err := decode(reader, &result); err != nil {
		return result, err
	}
	return result, result.Validate()
}

func ReadConnector(reader io.Reader) (Connector, error) {
	var result Connector
	if err := decode(reader, &result); err != nil {
		return result, err
	}
	return result, result.Validate()
}

// Go's JSON decoder normally accepts duplicate keys and case-insensitive field
// aliases. Reject both, along with nulls and unknown nested fields, before decode.
func decode(reader io.Reader, target any) error {
	data, err := io.ReadAll(io.LimitReader(reader, MaxConfigBytes+1))
	if err != nil || len(data) > MaxConfigBytes {
		return errors.New("configuration unreadable or too large")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	if err := shape(d, reflect.TypeOf(target).Elem(), 0); err != nil {
		return errors.New("invalid configuration JSON shape")
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("trailing configuration data")
	}
	if err := json.Unmarshal(data, target); err != nil {
		return errors.New("invalid configuration value type")
	}
	return nil
}

func shape(d *json.Decoder, kind reflect.Type, depth int) error {
	if depth > 16 {
		return errors.New("configuration nesting limit")
	}
	token, err := d.Token()
	if err != nil || token == nil {
		return errors.New("missing value")
	}
	switch kind.Kind() {
	case reflect.Struct:
		if token != json.Delim('{') {
			return errors.New("object required")
		}
		fields := map[string]reflect.Type{}
		for i := 0; i < kind.NumField(); i++ {
			field := kind.Field(i)
			fields[strings.Split(field.Tag.Get("json"), ",")[0]] = field.Type
		}
		seen := map[string]bool{}
		for d.More() {
			key, err := d.Token()
			if err != nil {
				return err
			}
			name, ok := key.(string)
			field, exists := fields[name]
			if !ok || !exists || seen[name] {
				return errors.New("unknown or duplicate field")
			}
			seen[name] = true
			if err := shape(d, field, depth+1); err != nil {
				return err
			}
		}
		end, err := d.Token()
		if err != nil || end != json.Delim('}') {
			return errors.New("object not closed")
		}
	case reflect.Slice:
		if token != json.Delim('[') {
			return errors.New("array required")
		}
		for d.More() {
			if err := shape(d, kind.Elem(), depth+1); err != nil {
				return err
			}
		}
		end, err := d.Token()
		if err != nil || end != json.Delim(']') {
			return errors.New("array not closed")
		}
	default:
		if _, delimiter := token.(json.Delim); delimiter {
			return fmt.Errorf("scalar required")
		}
	}
	return nil
}
