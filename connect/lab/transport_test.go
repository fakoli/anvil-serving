package lab

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func reverseHTTP(t *testing.T, f *fixture, origin *httptest.Server) (*child, string) {
	t.Helper()
	connector := f.connector(t, strings.TrimPrefix(origin.URL, "http://"), f.reversePort, f.client, f.caPath)
	awaitListener(t, "127.0.0.1:"+f.reversePort, connector, f.server)
	return connector, "http://127.0.0.1:" + f.reversePort
}

func requestClient(t *testing.T) *http.Client {
	t.Helper()
	transport := &http.Transport{DisableKeepAlives: true, ResponseHeaderTimeout: 5 * time.Second}
	t.Cleanup(transport.CloseIdleConnections)
	return &http.Client{Transport: transport, Timeout: 8 * time.Second}
}

func TestExplicitConnectProxy(t *testing.T) {
	f := newFixture(t)
	var connects atomic.Int32
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodConnect || r.Host != f.serverAddress {
			http.Error(w, "destination denied", http.StatusForbidden)
			return
		}
		upstream, err := net.DialTimeout("tcp4", f.serverAddress, time.Second)
		if err != nil {
			http.Error(w, "fixture unavailable", http.StatusBadGateway)
			return
		}
		defer upstream.Close()
		client, buffered, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer client.Close()
		_ = client.SetDeadline(time.Now().Add(8 * time.Second))
		_ = upstream.SetDeadline(time.Now().Add(8 * time.Second))
		_, _ = buffered.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n")
		if err := buffered.Flush(); err != nil {
			return
		}
		connects.Add(1)
		done := make(chan struct{})
		go func() {
			_, _ = io.Copy(upstream, buffered)
			upstream.Close()
			close(done)
		}()
		_, _ = io.Copy(client, upstream)
		client.Close()
		<-done
	}))
	defer proxy.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "through CONNECT and verified mutual TLS")
	}))
	defer origin.Close()
	connector := f.connector(t, strings.TrimPrefix(origin.URL, "http://"), f.reversePort, f.client, f.caPath, "--http-proxy", strings.TrimPrefix(proxy.URL, "http://"))
	defer connector.stop()
	awaitListener(t, "127.0.0.1:"+f.reversePort, connector, f.server)
	response, err := requestClient(t).Get("http://127.0.0.1:" + f.reversePort)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil || string(body) != "through CONNECT and verified mutual TLS" || connects.Load() == 0 {
		t.Fatalf("CONNECT proxy path failed: count=%d body=%q err=%v", connects.Load(), body, err)
	}
	t.Log("qualified a synthetic HTTP CONNECT proxy passing opaque TLS; TLS interception and buffering HTTP proxies remain unqualified")
}

func TestSSEFlushThroughReverseTunnel(t *testing.T) {
	f := newFixture(t)
	allowSecond := make(chan struct{})
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		select {
		case <-allowSecond:
			_, _ = io.WriteString(w, "data: second\n\n")
		case <-r.Context().Done():
		}
	}))
	defer origin.Close()
	_, endpoint := reverseHTTP(t, f, origin)
	start := time.Now()
	response, err := requestClient(t).Get(endpoint + "/events")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	reader := bufio.NewReader(response.Body)
	line, err := reader.ReadString('\n')
	if err != nil || line != "data: first\n" {
		t.Fatalf("first event %q: %v", line, err)
	}
	blank, err := reader.ReadString('\n')
	if err != nil || blank != "\n" {
		t.Fatalf("event boundary %q: %v", blank, err)
	}
	t.Logf("first event arrived while origin was still waiting: %s", time.Since(start))
	close(allowSecond)
	rest, err := io.ReadAll(reader)
	if err != nil || string(rest) != "data: second\n\n" {
		t.Fatalf("remaining events %q: %v", rest, err)
	}
}

func TestWebSocketThroughReverseTunnel(t *testing.T) {
	f := newFixture(t)
	serverDone := make(chan struct{})
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(serverDone)
		conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"anvil-lab.v1"}, CompressionMode: websocket.CompressionDisabled})
		if err != nil {
			return
		}
		defer conn.CloseNow()
		conn.SetReadLimit(128 * 1024)
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		for {
			kind, data, err := conn.Read(ctx)
			if err != nil {
				return
			}
			if err := conn.Write(ctx, kind, data); err != nil {
				return
			}
		}
	}))
	defer origin.Close()
	_, endpoint := reverseHTTP(t, f, origin)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, strings.Replace(endpoint, "http://", "ws://", 1)+"/socket", &websocket.DialOptions{HTTPClient: requestClient(t), Subprotocols: []string{"anvil-lab.v1"}, CompressionMode: websocket.CompressionDisabled})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.CloseNow()
	conn.SetReadLimit(128 * 1024)
	if conn.Subprotocol() != "anvil-lab.v1" {
		t.Fatal("subprotocol was not preserved")
	}
	for _, message := range []struct {
		kind websocket.MessageType
		data []byte
	}{
		{websocket.MessageText, []byte("dashboard event")},
		{websocket.MessageBinary, bytes.Repeat([]byte{0, 1, 127, 255}, 16384)},
	} {
		if err := conn.Write(ctx, message.kind, message.data); err != nil {
			t.Fatal(err)
		}
		kind, data, err := conn.Read(ctx)
		if err != nil || kind != message.kind || !bytes.Equal(data, message.data) {
			t.Fatalf("WebSocket echo changed: kind=%v length=%d error=%v", kind, len(data), err)
		}
	}
	if err := conn.Close(websocket.StatusNormalClosure, "done"); err != nil {
		t.Fatal(err)
	}
	select {
	case <-serverDone:
	case <-time.After(2 * time.Second):
		t.Fatal("WebSocket close did not reach origin")
	}
}

func TestClientCancellationReachesOrigin(t *testing.T) {
	f := newFixture(t)
	stopped := make(chan struct{})
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(stopped)
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: admitted\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer origin.Close()
	_, endpoint := reverseHTTP(t, f, origin)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint+"/events", nil)
	if err != nil {
		t.Fatal(err)
	}
	response, err := requestClient(t).Do(request)
	if err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	defer response.Body.Close()
	cancel()
	select {
	case <-stopped:
		t.Logf("origin HTTP context cancelled after %s", time.Since(start))
	case <-time.After(2 * time.Second):
		f.server.stop()
		t.Fatal("client cancellation did not reach origin within 2 seconds")
	}
}

func TestDisconnectDoesNotReplayPost(t *testing.T) {
	f := newFixture(t)
	var executions atomic.Int32
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			_, _ = fmt.Fprint(w, executions.Load())
			return
		}
		body, err := io.ReadAll(io.LimitReader(r.Body, 1024))
		if err != nil || string(body) != "synthetic generation" {
			http.Error(w, "bad fixture request", 400)
			return
		}
		executions.Add(1)
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: partial\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer origin.Close()
	connector, endpoint := reverseHTTP(t, f, origin)
	client := requestClient(t)
	response, err := client.Post(endpoint+"/generate", "text/plain", strings.NewReader("synthetic generation"))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	reader := bufio.NewReader(response.Body)
	line, err := reader.ReadString('\n')
	if err != nil || line != "data: partial\n" {
		t.Fatalf("missing admitted response: %q %v", line, err)
	}
	connector.stop()
	_, err = io.ReadAll(reader)
	if err == nil {
		t.Fatal("a truncated streaming response appeared complete")
	}
	_, _ = reverseHTTP(t, f, origin)
	check, err := client.Get(endpoint + "/count")
	if err != nil {
		t.Fatal(err)
	}
	defer check.Body.Close()
	count, err := io.ReadAll(check.Body)
	if err != nil || string(count) != "1" {
		t.Fatalf("origin executions after reconnect: %q %v", count, err)
	}
	if executions.Load() != 1 {
		t.Fatal("transport repeated an origin execution")
	}
}

func TestReverseResourceRestrictions(t *testing.T) {
	for _, invalid := range []string{"identity", "bind-port"} {
		t.Run(invalid, func(t *testing.T) {
			f := newFixture(t)
			origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("denied connector reached origin") }))
			defer origin.Close()
			id, port := f.client, f.reversePort
			if invalid == "identity" {
				id = f.ca.issue(t, f.dir, "connector-b", false)
			} else {
				port = loopbackPort(t)
			}
			bad := f.connector(t, strings.TrimPrefix(origin.URL, "http://"), port, id, f.caPath)
			deadline := time.Now().Add(4 * time.Second)
			for time.Now().Before(deadline) {
				log := strings.ToLower(bad.log.String() + f.server.log.String())
				if strings.Contains(log, "restrict") || strings.Contains(log, "not allowed") || strings.Contains(log, "forbidden") {
					conn, err := net.DialTimeout("tcp4", "127.0.0.1:"+port, 100*time.Millisecond)
					if err == nil {
						conn.Close()
						t.Fatal("denied reverse listener was created")
					}
					bad.stop()
					_, endpoint := reverseHTTP(t, f, origin)
					// A successful TCP connection alone proves the allowed listener
					// exists; do not dispatch an HTTP request to the denied-origin fixture.
					conn, err = net.DialTimeout("tcp4", strings.TrimPrefix(endpoint, "http://"), time.Second)
					if err != nil {
						t.Fatal(err)
					}
					conn.Close()
					return
				}
				time.Sleep(20 * time.Millisecond)
			}
			t.Fatalf("no explicit restriction rejection observed: client=%s server=%s", bad.log.String(), f.server.log.String())
		})
	}
}

func TestSlowReaderBackpressure(t *testing.T) {
	f := newFixture(t)
	var written atomic.Int64
	finished := make(chan error, 1)
	const maximum = 64 * 1024 * 1024
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := http.NewResponseController(w).SetWriteDeadline(time.Now().Add(2 * time.Second)); err != nil {
			finished <- err
			return
		}
		chunk := make([]byte, 32*1024)
		w.Header().Set("Content-Type", "application/octet-stream")
		for written.Load() < maximum {
			n, err := w.Write(chunk)
			written.Add(int64(n))
			if err != nil {
				finished <- err
				return
			}
			w.(http.Flusher).Flush()
		}
		finished <- nil
	}))
	defer origin.Close()
	_, endpoint := reverseHTTP(t, f, origin)
	conn, err := net.DialTimeout("tcp4", strings.TrimPrefix(endpoint, "http://"), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.(*net.TCPConn).SetReadBuffer(4096); err != nil {
		t.Fatal(err)
	}
	_ = conn.SetDeadline(time.Now().Add(4 * time.Second))
	_, err = fmt.Fprintf(conn, "GET /large HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
	if err != nil {
		t.Fatal(err)
	}
	response, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		t.Fatal(err)
	}
	// Deliberately do not read the body. Closing the socket below releases it.
	_ = response
	select {
	case err := <-finished:
		var timeout net.Error
		if !errors.As(err, &timeout) || !timeout.Timeout() {
			t.Fatalf("expected blocked origin write to hit its deadline, got %v", err)
		}
		if total := written.Load(); total <= 0 || total >= maximum {
			t.Fatalf("unexpected buffered byte count %d", total)
		}
		t.Logf("origin write deadline reached at %d bytes including kernel/proxy buffering; this is not a 256-KiB application-buffer qualification", written.Load())
	case <-time.After(3 * time.Second):
		t.Fatal("origin did not complete with its bounded write deadline")
	}
}
