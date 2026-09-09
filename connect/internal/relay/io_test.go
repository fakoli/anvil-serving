package relay

import (
	"bufio"
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestCompletedBodyPreservesHTTP1Connection(t *testing.T) {
	var connections atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), time.Second)
		defer cancel()
		body := Body(w, r.Body, ctx, time.Second)
		defer body.Close()
		if _, err := io.Copy(io.Discard, body); err != nil {
			t.Error(err)
		}
		io.WriteString(w, "complete")
	}))
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	server.Start()
	defer server.Close()
	client := server.Client()
	for i := 0; i < 2; i++ {
		response, err := client.Post(server.URL, "text/plain", strings.NewReader("declared body"))
		if err != nil {
			t.Fatal(err)
		}
		_, err = io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if err != nil || response.StatusCode != 200 {
			t.Fatal("normal response interrupted", err)
		}
	}
	if connections.Load() != 1 {
		t.Fatal("completed body discarded keepalive connection")
	}
}

func TestHTTP2StalledUploadDoesNotCancelSiblingStream(t *testing.T) {
	var connections atomic.Int32
	release := make(chan struct{})
	var once sync.Once
	finish := func() { once.Do(func() { close(release) }) }
	defer finish()
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		body := Body(w, r.Body, ctx, time.Second)
		defer body.Close()
		if r.Method == "POST" {
			io.Copy(io.Discard, body)
			return
		}
		if _, err := io.Copy(io.Discard, body); err != nil {
			t.Error(err)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: first\n")
		w.(http.Flusher).Flush()
		select {
		case <-release:
			io.WriteString(w, "data: second\n")
		case <-ctx.Done():
		}
	}))
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	server.EnableHTTP2 = true
	server.StartTLS()
	defer server.Close()
	client := server.Client()
	client.Timeout = 4 * time.Second
	tr := client.Transport.(*http.Transport)
	tr.MaxConnsPerHost = 1
	tr.Protocols = new(http.Protocols)
	tr.Protocols.SetHTTP2(true)
	response, err := client.Get(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.ProtoMajor != 2 {
		t.Fatal("fixture did not use HTTP/2")
	}
	reader := bufio.NewReader(response.Body)
	line, err := reader.ReadString('\n')
	if err != nil || line != "data: first\n" {
		t.Fatal(err)
	}
	input, writer := io.Pipe()
	defer writer.Close()
	defer input.Close()
	request, err := http.NewRequest("POST", server.URL, input)
	if err != nil {
		t.Fatal(err)
	}
	request.ContentLength = 1
	done := make(chan struct{})
	go func() {
		defer close(done)
		result, _ := client.Do(request)
		if result != nil {
			result.Body.Close()
		}
	}()
	select {
	case <-done:
	case <-time.After(2500 * time.Millisecond):
		t.Fatal("stalled upload survived read deadline")
	}
	finish()
	line, err = reader.ReadString('\n')
	if err != nil || line != "data: second\n" {
		t.Fatal("sibling stream was reset", err)
	}
	if connections.Load() != 1 {
		t.Fatal("HTTP/2 streams were not multiplexed on one connection")
	}
}
