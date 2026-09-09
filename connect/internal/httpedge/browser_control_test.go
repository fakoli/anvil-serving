package httpedge

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

type blockingBrowserAuthority struct {
	*browserAuthorityStub
	entered chan context.Context
	release chan struct{}
	count   atomic.Int32
	fail    bool
}

func (a *blockingBrowserAuthority) Complete(ctx context.Context, callback session.Callback) (session.Completion, error) {
	a.count.Add(1)
	a.entered <- ctx
	select {
	case <-a.release:
		if a.fail {
			return session.Completion{}, session.ErrDenied
		}
		return a.browserAuthorityStub.Complete(ctx, callback)
	case <-ctx.Done():
		return session.Completion{}, ctx.Err()
	}
}

func TestBrowserControlBudgetBoundsExchangeAndAllowsLogout(t *testing.T) {
	for _, mode := range []string{"success", "error", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			a := &blockingBrowserAuthority{browserAuthorityStub: newBrowserAuthorityStub(), entered: make(chan context.Context, 2), release: make(chan struct{}), fail: mode == "error"}
			a.binding = "bound"
			b, err := NewBrowser(browserDeclaration("none"), a, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {
				t.Error("control reached app")
			})
			if err != nil {
				t.Fatal(err)
			}
			defer b.Close()
			callback := func() *http.Request {
				r := browserRequest("GET", BrowserCallbackPath+"?state=state&code=code", nil)
				r.AddCookie(&http.Cookie{Name: BrowserTransactionCookie, Value: "bound"})
				return r
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			finished := make(chan struct{})
			first := httptest.NewRecorder()
			go func() { defer close(finished); b.ServeHTTP(first, callback().WithContext(ctx)) }()
			var running context.Context
			select {
			case running = <-a.entered:
			case <-time.After(time.Second):
				t.Fatal("callback never entered")
			}
			deadline, ok := running.Deadline()
			if !ok || time.Until(deadline) > 5*time.Second {
				t.Fatal("callback context lacks bounded deadline")
			}
			second := httptest.NewRecorder()
			b.ServeHTTP(second, callback())
			if second.Code != 429 || a.count.Load() != 1 {
				t.Fatal("callback fanout bypassed control capacity")
			}
			logout := browserRequest("POST", BrowserLogoutPath, nil)
			logout.Header.Set("Origin", "https://dash.example.test")
			logout.AddCookie(&http.Cookie{Name: BrowserSessionCookie, Value: "opaque-session"})
			loggedOut := httptest.NewRecorder()
			b.ServeHTTP(loggedOut, logout)
			if loggedOut.Code != 303 {
				t.Fatal("blocked callback prevented logout")
			}
			if mode == "cancel" {
				cancel()
			} else {
				close(a.release)
			}
			select {
			case <-finished:
			case <-time.After(time.Second):
				t.Fatal("callback slot did not release")
			}
			if mode == "cancel" {
				close(a.release)
			}
			third := httptest.NewRecorder()
			b.ServeHTTP(third, callback())
			if third.Code == 429 || a.count.Load() != 2 {
				t.Fatal("completed exchange leaked control capacity")
			}
		})
	}
}
