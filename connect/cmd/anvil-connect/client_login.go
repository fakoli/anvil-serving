package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/base32"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
	"github.com/fakoli/anvil-serving/connect/internal/config"
)

var (
	errDeviceLoginUnavailable  = errors.New("device login unavailable")
	errDeviceApprovalDenied    = errors.New("device approval denied")
	errDeviceApprovalExpired   = errors.New("device approval expired")
	errDeviceApprovalCancelled = errors.New("device approval cancelled")
)

type deviceStartResponse struct {
	Status     string `json:"status"`
	DeviceCode string `json:"device_code"`
	UserCode   string `json:"user_code"`
	ExpiresIn  int    `json:"expires_in"`
	Interval   int    `json:"interval"`
}

type devicePollResponse struct {
	Status      string `json:"status"`
	AccessToken string `json:"access_token"`
	ExpiresIn   int    `json:"expires_in"`
	Interval    int    `json:"interval"`
}
type deviceApproval struct {
	token     string
	expiresAt time.Time
}

// loginClient obtains one short-lived remote key through the fixed browser
// approval endpoint and passes it directly to the existing loopback server.
// The device code and bearer never reach argv, environment, a file, or output.
func loginClient(ctx context.Context, declaration clientconfig.Config, lookup func(string) (string, bool), out io.Writer) error {
	device := declaration.DeviceAuthorization
	if ctx == nil || device == nil || lookup == nil {
		return errDeviceLoginUnavailable
	}
	localKey, ok := lookup(declaration.LocalKeyEnv)
	if !ok {
		return errDeviceLoginUnavailable
	}
	// Validate the existing loopback credential before a browser approval can
	// create a one-time remote credential that this client cannot use.
	probe, err := client.New(declaration.Rule, declaration.Listen, localKey, declaration.RemoteKeyEnv, func(name string) (string, bool) {
		if name == declaration.LocalKeyEnv {
			return localKey, true
		}
		return "", false
	}, client.Options{})
	if err != nil {
		return errDeviceLoginUnavailable
	}
	probe.Close()
	base := &url.URL{Scheme: "https", Host: device.BrowserHost, Path: device.ApprovalPath}
	if base.String() != "https://"+device.BrowserHost+device.ApprovalPath {
		return errDeviceLoginUnavailable
	}
	transport := &http.Transport{Proxy: nil, DisableKeepAlives: true, DisableCompression: true, TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 5 * time.Second, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, ServerName: device.BrowserHost}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errDeviceLoginUnavailable }}
	startURL := base.JoinPath("start")
	start, err := devicePOST[deviceStartResponse](ctx, client, startURL, nil)
	if err != nil || start.Status != "pending" || !canonicalDeviceCode(start.DeviceCode) || !canonicalUserCode(start.UserCode) || start.ExpiresIn < 1 || start.ExpiresIn > int((10*time.Minute).Seconds()) || start.Interval != int((5*time.Second).Seconds()) {
		return errDeviceLoginUnavailable
	}
	if json.NewEncoder(out).Encode(map[string]string{
		"verification_uri": base.String(),
		"user_code":        start.UserCode,
		"instruction":      "Open verification_uri in a browser, sign in, and enter user_code to approve this login.",
	}) != nil {
		return errDeviceLoginUnavailable
	}
	deviceCode := start.DeviceCode
	defer func() {
		if ctx.Err() == nil {
			return
		}
		cancel, stop := context.WithTimeout(context.Background(), 2*time.Second)
		defer stop()
		_, _ = devicePOST[devicePollResponse](cancel, client, base.JoinPath("cancel"), deviceRequest{DeviceCode: deviceCode})
	}()
	approval, err := awaitDeviceApproval(ctx, time.Duration(start.ExpiresIn)*time.Second, 5*time.Second, 30*time.Second, func(pollCtx context.Context) (devicePollResponse, error) {
		return devicePOST[devicePollResponse](pollCtx, client, base.JoinPath("poll"), deviceRequest{DeviceCode: deviceCode})
	})
	if errors.Is(err, context.Canceled) {
		return nil
	}
	if err != nil {
		return err
	}
	remoteKey := approval.token
	return serveClient(ctx, declaration, func(name string) (string, bool) {
		switch name {
		case declaration.LocalKeyEnv:
			return localKey, true
		case declaration.RemoteKeyEnv:
			return remoteKey, true
		default:
			return "", false
		}
	}, func() error {
		return json.NewEncoder(out).Encode(map[string]string{"mode": "client", "status": "running", "session_id": strings.Split(remoteKey, ".")[1], "local_base_url": "http://" + declaration.Listen, "expires_at": approval.expiresAt.UTC().Format(time.RFC3339)})
	})
}

// awaitDeviceApproval polls at the advertised base cadence. Every slow_down
// adds one base interval, capped by maxDelay, and the longer cadence persists
// across later pending responses. The polling context cannot outlive expiry.
func awaitDeviceApproval(ctx context.Context, lifetime, baseDelay, maxDelay time.Duration, poll func(context.Context) (devicePollResponse, error)) (deviceApproval, error) {
	if ctx == nil || poll == nil || lifetime <= 0 || baseDelay <= 0 || maxDelay < baseDelay {
		return deviceApproval{}, errDeviceLoginUnavailable
	}
	expiresAt := time.Now().Add(lifetime)
	expiry := time.NewTimer(lifetime)
	defer expiry.Stop()
	timer := time.NewTimer(baseDelay)
	defer timer.Stop()
	delay := baseDelay
	for {
		select {
		case <-ctx.Done():
			return deviceApproval{}, ctx.Err()
		case <-expiry.C:
			return deviceApproval{}, errDeviceApprovalExpired
		case <-timer.C:
		}
		pollCtx, cancel := context.WithDeadline(ctx, expiresAt)
		response, err := poll(pollCtx)
		cancel()
		if err != nil {
			if time.Now().Compare(expiresAt) >= 0 {
				return deviceApproval{}, errDeviceApprovalExpired
			}
			if ctx.Err() != nil {
				return deviceApproval{}, ctx.Err()
			}
			return deviceApproval{}, errDeviceLoginUnavailable
		}
		if time.Now().Compare(expiresAt) >= 0 {
			return deviceApproval{}, errDeviceApprovalExpired
		}
		switch response.Status {
		case "pending":
			if response.Interval != int(baseDelay.Seconds()) {
				return deviceApproval{}, errDeviceLoginUnavailable
			}
		case "slow_down":
			if response.Interval != int(baseDelay.Seconds()) {
				return deviceApproval{}, errDeviceLoginUnavailable
			}
			delay += baseDelay
			if delay > maxDelay {
				delay = maxDelay
			}
		case "approved":
			if !usableDeviceToken(response.AccessToken) || response.ExpiresIn < 1 || response.ExpiresIn > int(time.Hour.Seconds()) {
				return deviceApproval{}, errDeviceLoginUnavailable
			}
			return deviceApproval{token: response.AccessToken, expiresAt: time.Now().Add(time.Duration(response.ExpiresIn) * time.Second)}, nil
		case "denied":
			return deviceApproval{}, errDeviceApprovalDenied
		case "cancelled":
			return deviceApproval{}, errDeviceApprovalCancelled
		case "expired":
			return deviceApproval{}, errDeviceApprovalExpired
		case "redeemed":
			return deviceApproval{}, errDeviceLoginUnavailable
		default:
			return deviceApproval{}, errDeviceLoginUnavailable
		}
		if time.Now().Compare(expiresAt) >= 0 {
			return deviceApproval{}, errDeviceApprovalExpired
		}
		timer.Reset(delay)
	}
}

func loginFailure(diagnostics io.Writer, err error) int {
	if diagnostics == nil {
		return 1
	}
	switch {
	case errors.Is(err, errDeviceApprovalDenied):
		_, _ = fmt.Fprintln(diagnostics, "anvil-connect: device approval was denied")
	case errors.Is(err, errDeviceApprovalExpired):
		_, _ = fmt.Fprintln(diagnostics, "anvil-connect: device approval expired; run login again")
	case errors.Is(err, errDeviceApprovalCancelled):
		_, _ = fmt.Fprintln(diagnostics, "anvil-connect: device login was cancelled")
	default:
		_, _ = fmt.Fprintln(diagnostics, "anvil-connect: device login unavailable")
	}
	return 1
}

type deviceRequest struct {
	DeviceCode string `json:"device_code"`
}

func devicePOST[T any](ctx context.Context, client *http.Client, endpoint *url.URL, value any) (T, error) {
	var zero T
	if ctx == nil || client == nil || endpoint == nil || endpoint.Scheme != "https" || endpoint.Host == "" || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return zero, errors.New("device request unavailable")
	}
	body := []byte{}
	if value != nil {
		var err error
		body, err = json.Marshal(value)
		if err != nil {
			return zero, errors.New("device request unavailable")
		}
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return zero, errors.New("device request unavailable")
	}
	request.Header.Set("User-Agent", "anvil-connect-device-login/1")
	if value != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := client.Do(request)
	if err != nil {
		return zero, errors.New("device request unavailable")
	}
	defer response.Body.Close()
	if (response.StatusCode != http.StatusOK && response.StatusCode != http.StatusTooManyRequests) || len(response.Header.Values("Content-Type")) != 1 || response.Header.Get("Content-Type") != "application/json" {
		return zero, errors.New("device request unavailable")
	}
	body, err = io.ReadAll(io.LimitReader(response.Body, 2049))
	if err != nil || len(body) > 2048 {
		return zero, errors.New("device request unavailable")
	}
	var result T
	if config.Decode(bytes.NewReader(body), &result) != nil {
		return zero, errors.New("device request unavailable")
	}
	return result, nil
}

func canonicalDeviceCode(value string) bool { return canonicalBase32(value, 52) }
func canonicalUserCode(value string) bool   { return canonicalBase32(value, 8) }
func canonicalBase32(value string, length int) bool {
	if len(value) != length {
		return false
	}
	decoded, err := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(value)
	return err == nil && len(decoded) == 5*length/8 && base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(decoded) == value
}
func usableDeviceToken(value string) bool {
	parts := strings.Split(value, ".")
	if len(parts) != 3 || parts[0] != "acd1" || len(parts[1]) != 32 || len(parts[2]) != 43 {
		return false
	}
	id, err := hex.DecodeString(parts[1])
	if err != nil || hex.EncodeToString(id) != parts[1] {
		return false
	}
	secret, err := base64.RawURLEncoding.DecodeString(parts[2])
	return err == nil && len(secret) == 32 && base64.RawURLEncoding.EncodeToString(secret) == parts[2]
}
