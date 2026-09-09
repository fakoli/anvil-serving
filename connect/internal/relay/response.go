package relay

import (
	"errors"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"net/http"
	"net/url"
	"strings"
)

// APIResponse confines native redirects and strips credentials before delivery.
func APIResponse(response *http.Response, rule config.Rule) error {
	locations := response.Header.Values("Location")
	if len(locations) > 1 {
		return errors.New("ambiguous origin redirect")
	}
	if len(locations) == 1 {
		u, err := url.Parse(locations[0])
		if err != nil || u.User != nil || u.Opaque != "" || strings.HasPrefix(locations[0], "//") {
			return errors.New("unsafe origin redirect")
		}
		if u.IsAbs() && (u.Scheme != "https" || u.Host != rule.Host) {
			return errors.New("origin redirect escaped resource")
		}
		if !config.CanonicalPath(u.Path) || !rule.Allows(rule.Host, u.Path, response.Request.Method) {
			return errors.New("origin redirect escaped path")
		}
	}
	for name := range response.Header {
		lower := strings.ToLower(name)
		switch lower {
		case "content-type", "content-length", "content-encoding", "content-disposition", "location", "retry-after", "www-authenticate", "x-request-id", "openai-processing-ms", "connection", "upgrade", "sec-websocket-accept", "sec-websocket-protocol", "sec-websocket-extensions":
		default:
			if !strings.HasPrefix(lower, "x-ratelimit-") {
				delete(response.Header, name)
			}
		}
	}
	response.Header.Set("Cache-Control", "no-store")
	return nil
}
