package admin

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"golang.org/x/sys/unix"
)

// Call sends exactly one local administrative request over an owner-only Unix
// socket. The caller supplies a privatefiles.PinnedPath pathname and keeps its
// handle live for the call; this helper does not read environment state.
func Call(ctx context.Context, socketPath string, input Request) (Response, error) {
	if ctx == nil || !validSocket(socketPath) {
		return Response{}, ErrAdmin
	}
	input.Grants = append([]access.Grant{}, input.Grants...)
	input.Resources = append([]string{}, input.Resources...)
	body, err := json.Marshal(input)
	if err != nil || len(body) > maxBody {
		return Response{}, ErrAdmin
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	transport := &http.Transport{
		Proxy:                  nil,
		DisableKeepAlives:      true,
		ForceAttemptHTTP2:      false,
		ResponseHeaderTimeout:  5 * time.Second,
		MaxResponseHeaderBytes: maxBody,
		DialContext: func(dialContext context.Context, network, address string) (net.Conn, error) {
			if network != "tcp" || address != Host+":80" {
				return nil, ErrAdmin
			}
			connection, err := (&net.Dialer{}).DialContext(dialContext, "unix", socketPath)
			if err != nil || !peerOwned(connection) {
				if connection != nil {
					_ = connection.Close()
				}
				return nil, ErrAdmin
			}
			return connection, nil
		},
	}
	defer transport.CloseIdleConnections()
	endpoint := &url.URL{Scheme: "http", Host: Host, Path: Path}
	request := &http.Request{
		Method:        http.MethodPost,
		URL:           endpoint,
		Host:          Host,
		Header:        http.Header{"Content-Type": []string{"application/json"}},
		Body:          io.NopCloser(bytes.NewReader(body)),
		ContentLength: int64(len(body)),
		GetBody:       nil,
	}
	request = request.WithContext(ctx)
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return ErrAdmin }}
	response, err := client.Do(request)
	if err != nil {
		return Response{}, ErrAdmin
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || len(response.Header.Values("Content-Type")) != 1 || response.Header.Get("Content-Type") != "application/json" {
		return Response{}, ErrAdmin
	}
	var output Response
	if config.Decode(io.LimitReader(response.Body, maxBody+1), &output) != nil || output.Operation != input.Operation {
		return Response{}, ErrAdmin
	}
	return output, nil
}

func validSocket(path string) bool {
	if len(path) == 0 || len(path) > 107 || !strings.HasPrefix(path, "/proc/self/fd/") {
		return false
	}
	var stat unix.Stat_t
	return unix.Lstat(path, &stat) == nil && stat.Mode&unix.S_IFMT == unix.S_IFSOCK && stat.Mode&07777 == 0600 && stat.Uid == uint32(os.Geteuid())
}

func peerOwned(connection net.Conn) bool {
	unixConnection, ok := connection.(*net.UnixConn)
	if !ok {
		return false
	}
	raw, err := unixConnection.SyscallConn()
	if err != nil {
		return false
	}
	owned := false
	if raw.Control(func(fd uintptr) {
		credential, err := unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
		owned = err == nil && credential != nil && credential.Uid == uint32(os.Geteuid())
	}) != nil {
		return false
	}
	return owned
}
