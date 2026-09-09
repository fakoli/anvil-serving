package origin

import (
	"bufio"
	"io"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
)

func TestControlLeaseExpiryClosesActiveOriginAndDeniesNewAccess(t *testing.T) {
	stopped := make(chan struct{})
	var lookups, executions atomic.Int32
	f := proxyFixture(t, func(w http.ResponseWriter, r *http.Request) {
		executions.Add(1)
		defer close(stopped)
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}, func(string) (string, bool) {
		lookups.Add(1)
		return "synthetic-native-token", true
	})
	// The injected clock tests the 45-second authority boundary without waiting
	// 45 seconds. Stream cancellation still uses the real production poller.
	started := time.Now()
	var elapsed atomic.Int64
	binding := f.proxy.lease.Binding()
	lease, err := access.NewLease(binding, func() time.Time { return started.Add(time.Duration(elapsed.Load())) })
	if err != nil {
		t.Fatal(err)
	}
	if err := lease.Renew(binding, 1, started); err != nil {
		t.Fatal(err)
	}
	f.proxy.lease = lease // before any handler can observe the fixture
	response, err := f.client.Do(f.request(t, "GET", "/v1/models"))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	reader := bufio.NewReader(response.Body)
	if line, err := reader.ReadString('\n'); err != nil || line != "data: first\n" {
		t.Fatal("initial stream unavailable", err)
	}
	elapsed.Store(int64(access.ConnectorLeaseLifetime))
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("expired control lease left native request active")
	}
	if _, err := io.ReadAll(reader); err == nil {
		t.Fatal("expired stream was reported as a complete response")
	}
	denied, err := f.client.Do(f.request(t, "GET", "/v1/models"))
	if err != nil {
		t.Fatal(err)
	}
	denied.Body.Close()
	if denied.StatusCode != http.StatusServiceUnavailable || lookups.Load() != 1 || executions.Load() != 1 {
		t.Fatal("expired lease reached native authority or origin")
	}
}

func TestColdOrInvalidatedControlLeaseCannotReadNativeCredential(t *testing.T) {
	for _, cold := range []bool{true, false} {
		t.Run(map[bool]string{true: "cold", false: "invalidated"}[cold], func(t *testing.T) {
			var lookups, executions atomic.Int32
			f := proxyFixture(t, func(http.ResponseWriter, *http.Request) { executions.Add(1) }, func(string) (string, bool) {
				lookups.Add(1)
				return "synthetic-native-token", true
			})
			if cold {
				lease, err := access.NewLease(f.proxy.lease.Binding(), nil)
				if err != nil {
					t.Fatal(err)
				}
				f.proxy.lease = lease
			} else {
				f.proxy.lease.Invalidate()
			}
			response, err := f.client.Do(f.request(t, "GET", "/v1/models"))
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			if response.StatusCode != http.StatusServiceUnavailable || lookups.Load() != 0 || executions.Load() != 0 {
				t.Fatal("closed lease reached native authority or origin")
			}
		})
	}
}
