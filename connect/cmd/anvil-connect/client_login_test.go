package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestAwaitDeviceApprovalBacksOffAfterSlowDown(t *testing.T) {
	var calls []time.Time
	expectedToken := "acd1." + strings.Repeat("a", 32) + "." + base64.RawURLEncoding.EncodeToString(make([]byte, 32))
	token, err := awaitDeviceApproval(context.Background(), time.Second, 10*time.Millisecond, 30*time.Millisecond, func(context.Context) (devicePollResponse, error) {
		calls = append(calls, time.Now())
		switch len(calls) {
		case 1:
			return devicePollResponse{Status: "slow_down", Interval: 0}, nil
		case 2:
			return devicePollResponse{Status: "pending", Interval: 0}, nil
		default:
			return devicePollResponse{Status: "approved", AccessToken: expectedToken, ExpiresIn: 60}, nil
		}
	})
	if err != nil || token.token != expectedToken || len(calls) != 3 {
		t.Fatalf("token=%+v calls=%d err=%v", token, len(calls), err)
	}
	if calls[1].Sub(calls[0]) < 18*time.Millisecond || calls[2].Sub(calls[1]) < 18*time.Millisecond {
		t.Fatalf("slow_down did not retain backoff: %s, %s", calls[1].Sub(calls[0]), calls[2].Sub(calls[1]))
	}
}

func TestAwaitDeviceApprovalCancellationAndExpiry(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	called := false
	_, err := awaitDeviceApproval(ctx, time.Second, time.Second, time.Second, func(context.Context) (devicePollResponse, error) {
		called = true
		return devicePollResponse{}, nil
	})
	if !errors.Is(err, context.Canceled) || called {
		t.Fatalf("cancellation err=%v called=%v", err, called)
	}

	started := time.Now()
	_, err = awaitDeviceApproval(context.Background(), 10*time.Millisecond, time.Second, time.Second, func(context.Context) (devicePollResponse, error) {
		t.Fatal("expired approval must not poll")
		return devicePollResponse{}, nil
	})
	if !errors.Is(err, errDeviceApprovalExpired) || time.Since(started) > 200*time.Millisecond {
		t.Fatalf("expiry err=%v elapsed=%s", err, time.Since(started))
	}

	_, err = awaitDeviceApproval(context.Background(), 10*time.Millisecond, time.Millisecond, time.Second, func(context.Context) (devicePollResponse, error) {
		time.Sleep(15 * time.Millisecond)
		return devicePollResponse{Status: "approved", AccessToken: "late-token", ExpiresIn: 60}, nil
	})
	if !errors.Is(err, errDeviceApprovalExpired) {
		t.Fatalf("late approval err=%v", err)
	}
}

func TestLoginFailureClassifiesTerminalStatusWithoutSecrets(t *testing.T) {
	for _, tc := range []struct {
		err  error
		want string
	}{
		{errDeviceApprovalDenied, "denied"},
		{errDeviceApprovalExpired, "expired"},
		{errDeviceApprovalCancelled, "cancelled"},
		{errDeviceLoginUnavailable, "unavailable"},
	} {
		var output strings.Builder
		if code := loginFailure(&output, tc.err); code != 1 || !strings.Contains(output.String(), tc.want) {
			t.Fatalf("err=%v code=%d output=%q", tc.err, code, output.String())
		}
		if strings.Contains(output.String(), "device-code") || strings.Contains(output.String(), "access-token") {
			t.Fatalf("secret-like output: %q", output.String())
		}
	}
	if loginFailure(io.Discard, errDeviceApprovalDenied) != 1 {
		t.Fatal("discard diagnostic must still fail")
	}
}

func TestDevicePOSTRejectsAmbiguousResponsesAndUsesApplicationUA(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.UserAgent() != "anvil-connect-device-login/1" {
			t.Errorf("user agent=%q", r.UserAgent())
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"status":"pending","status":"approved"}`)
	}))
	defer server.Close()
	endpoint, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := devicePOST[devicePollResponse](context.Background(), server.Client(), endpoint, nil); err == nil {
		t.Fatal("duplicate device response fields accepted")
	}
}

func TestDeviceWireValuesAreCanonical(t *testing.T) {
	if !canonicalDeviceCode(strings.Repeat("A", 52)) || !canonicalUserCode("ABCDEFGH") {
		t.Fatal("canonical device codes rejected")
	}
	for _, value := range []string{strings.Repeat("a", 52), strings.Repeat("A", 51), strings.Repeat("A", 51) + "=", strings.Repeat("A", 51) + "B", "ABC\x00EFGH"} {
		if canonicalDeviceCode(value) || canonicalUserCode(value) {
			t.Fatal("malformed code accepted")
		}
	}
	good := "acd1." + strings.Repeat("a", 32) + "." + base64.RawURLEncoding.EncodeToString(make([]byte, 32))
	if !usableDeviceToken(good) {
		t.Fatal("canonical acd1 token rejected")
	}
	for _, token := range []string{strings.Replace(good, "acd1.", "ac1.", 1), good + "=", strings.Replace(good, ".", "_", 1)} {
		if usableDeviceToken(token) {
			t.Fatal("malformed token accepted")
		}
	}
}

func TestLoginOutputSupportsPeopleAndAutomation(t *testing.T) {
	const uri = "https://dashboard.example.test/observatory/_anvil-connect/device"
	const code = "ABCDEFGH"
	const base = "http://127.0.0.1:8787/v1"
	expires := time.Date(2026, 9, 10, 12, 0, 0, 0, time.UTC)
	for _, jsonOutput := range []bool{false, true} {
		var output strings.Builder
		if err := writeLoginChallenge(&output, jsonOutput, uri, code); err != nil {
			t.Fatal(err)
		}
		challenge := output.String()
		if !strings.Contains(challenge, uri) || !strings.Contains(challenge, code) || strings.Contains(challenge, "device_code") || strings.Contains(challenge, "access_token") {
			t.Fatal("challenge contract changed")
		}
		output.Reset()
		if err := writeLoginReady(&output, jsonOutput, base, "test-session", expires); err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(output.String(), base) || !strings.Contains(output.String(), expires.Format(time.RFC3339)) {
			t.Fatal("connection details missing")
		}
		if jsonOutput {
			var value map[string]string
			if err := json.Unmarshal([]byte(challenge), &value); err != nil || value["verification_uri"] != uri || value["user_code"] != code {
				t.Fatal("invalid challenge JSON")
			}
			if err := json.Unmarshal([]byte(output.String()), &value); err != nil || value["status"] != "running" || value["local_base_url"] != base {
				t.Fatal("invalid ready JSON")
			}
		} else if !strings.Contains(challenge, "Waiting for browser approval") || !strings.Contains(output.String(), "Ctrl+C") {
			t.Fatal("human instructions missing")
		}
	}
}
