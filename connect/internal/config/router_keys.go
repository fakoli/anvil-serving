package config

import (
	"errors"
	"net/url"
	"strings"
)

// RouterKeys declares the fixed broker destination; browser callers cannot set it.
type RouterKeys struct {
	URL       string `json:"url"`
	SecretEnv string `json:"secret_env"`
	CheckEnv  string `json:"check_env"`
}

func (r RouterKeys) Validate() error {
	u, err := url.Parse(r.URL)
	if err != nil || len(r.URL) > 2048 || !ValidEnv(r.SecretEnv) || !ValidEnv(r.CheckEnv) || r.SecretEnv == r.CheckEnv || u.Hostname() == "" || u.Hostname() == "localhost" || strings.HasSuffix(u.Hostname(), ".localhost") || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.RawPath != "" || u.Opaque != "" || (u.Path != "" && u.Path != "/") || strings.ContainsAny(r.URL, "\\\r\n\t #?") || (u.Scheme != "https" && (u.Scheme != "http" || u.Hostname() != "127.0.0.1")) {
		return errors.New("router keys require a fixed HTTPS or loopback HTTP URL and secret reference")
	}
	return nil
}
