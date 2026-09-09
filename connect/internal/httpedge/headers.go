package httpedge

import (
	"errors"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/fakoli/anvil-serving/connect/internal/config"
)

var ErrRequest = errors.New("invalid or ambiguous request")

func values(header http.Header, wanted string) []string {
	var result []string
	for name, entries := range header {
		if strings.EqualFold(name, wanted) {
			result = append(result, entries...)
		}
	}
	return result
}

func has(header http.Header, wanted string) bool {
	for name := range header {
		if strings.EqualFold(name, wanted) {
			return true
		}
	}
	return false
}

// ValidateHead supplements net/http's wire parser for callers and proxy hops.
// Never normalize a path or ambiguous identity into a more privileged request.
func ValidateHead(r *http.Request) error {
	if r.URL == nil || r.URL.IsAbs() || r.URL.Host != "" || r.URL.Opaque != "" || r.URL.Fragment != "" || r.URL.RawPath != "" || !config.ValidHost(r.Host) || !config.CanonicalPath(r.URL.Path) || !config.ValidMethod(r.Method) || len(r.RequestURI) > 8192 || len(r.URL.Path)+len(r.URL.RawQuery) > 8192 || strings.Contains(r.RequestURI, "#") {
		return ErrRequest
	}
	if has(r.Header, "Host") || has(r.Header, "Trailer") || len(r.Trailer) > 0 {
		return ErrRequest
	}
	size := 0
	for name, entries := range r.Header {
		if !validFieldName(name) {
			return ErrRequest
		}
		for _, value := range entries {
			if strings.IndexFunc(value, func(c rune) bool { return (c < 32 && c != '\t') || c == 127 }) >= 0 {
				return ErrRequest
			}
			size += len(name) + len(value) + 4
		}
	}
	if size > 65536 {
		return ErrRequest
	}
	upgrade := false
	for _, value := range values(r.Header, "Connection") {
		for _, token := range strings.Split(value, ",") {
			switch strings.ToLower(strings.TrimSpace(token)) {
			case "upgrade":
				upgrade = true
			case "keep-alive", "close":
			default:
				return ErrRequest // cannot nominate credentials as hop headers
			}
		}
	}
	upgrades := values(r.Header, "Upgrade")
	if upgrade || len(upgrades) > 0 {
		if !upgrade || len(upgrades) != 1 || !strings.EqualFold(upgrades[0], "websocket") || r.Method != "GET" {
			return ErrRequest
		}
	}
	lengths := values(r.Header, "Content-Length")
	if len(lengths) > 1 {
		return ErrRequest
	}
	if len(lengths) == 1 {
		n, err := strconv.ParseInt(lengths[0], 10, 64)
		if err != nil || n < 0 || strconv.FormatInt(n, 10) != lengths[0] || n != r.ContentLength {
			return ErrRequest
		}
	}
	if has(r.Header, "Transfer-Encoding") {
		return ErrRequest
	} // parser must own framing
	// net/http removes Content-Length when parsing TE+CL. Reject all HTTP/1
	// transfer-coded requests so that erased ambiguity cannot become admission.
	// HTTP/2 bodies can have unknown lengths without ambiguous transfer coding.
	if len(r.TransferEncoding) > 0 || (r.ProtoMajor < 2 && r.ContentLength < 0) {
		return ErrRequest
	}
	query, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return ErrRequest
	}
	for name := range query {
		switch strings.ToLower(name) {
		case "access_token", "api_key", "authorization":
			return ErrRequest
		}
	}
	return nil
}

func validFieldName(name string) bool {
	if name == "" {
		return false
	}
	for _, c := range name {
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || strings.ContainsRune("!#$%&'*+-.^_`|~", c) {
			continue
		}
		return false
	}
	return true
}

// APIKey accepts the two common SDK carriers, but never combines credentials.
// Browser sessions use their own admission path and cannot select this profile.
func APIKey(r *http.Request) (string, error) {
	if has(r.Header, "Origin") || has(r.Header, "Cookie") || has(r.Header, "Proxy-Authorization") {
		return "", ErrRequest
	}
	auth, api := values(r.Header, "Authorization"), values(r.Header, "X-Api-Key")
	if len(auth)+len(api) != 1 {
		return "", ErrRequest
	}
	if len(api) == 1 {
		if strings.TrimSpace(api[0]) != api[0] {
			return "", ErrRequest
		}
		return api[0], nil
	}
	if !strings.HasPrefix(auth[0], "Bearer ") || strings.ContainsAny(strings.TrimPrefix(auth[0], "Bearer "), " \t,") {
		return "", ErrRequest
	}
	return strings.TrimPrefix(auth[0], "Bearer "), nil
}

// CleanAPIHeaders retains only the supported API protocol headers. A denylist
// cannot cover identity and route-override headers from every proxy/framework.
// Browser resources have a separate cookie and header contract.
func CleanAPIHeaders(header http.Header) {
	for name := range header {
		lower := strings.ToLower(name)
		switch lower {
		case "content-type", "content-length", "accept", "accept-encoding", "user-agent",
			"anthropic-version", "anthropic-beta", "openai-beta", "last-event-id", "x-request-id",
			"connection", "upgrade", "sec-websocket-key", "sec-websocket-version", "sec-websocket-protocol", "sec-websocket-extensions":
		default:
			delete(header, name)
		}
	}
}
