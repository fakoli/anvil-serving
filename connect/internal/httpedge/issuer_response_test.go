package httpedge

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/session"
)

type issuerAuthority struct {
	*browserAuthorityStub
	issuer string
}

func (a *issuerAuthority) Complete(ctx context.Context, callback session.Callback) (session.Completion, error) {
	a.issuer = callback.Issuer
	return a.browserAuthorityStub.Complete(ctx, callback)
}

func TestBrowserIssuerAndScopeHaveClosedQueryGrammar(t *testing.T) {
	authority := &issuerAuthority{browserAuthorityStub: newBrowserAuthorityStub()}
	authority.binding = "binding"
	browser, err := NewBrowser(browserDeclaration("none"), authority, func(http.ResponseWriter, *http.Request, config.Resource, session.Admission) {
		t.Error("callback dispatched to origin")
	})
	if err != nil {
		t.Fatal(err)
	}
	defer browser.Close()
	for _, suffix := range []string{"&iss=", "&iss=a&iss=b", "&scope=openid&scope=openid", "&scope=email", "&unknown=ignored"} {
		r := browserRequest(http.MethodGet, BrowserCallbackPath+"?state=state&code=code"+suffix, nil)
		r.Header.Set("Cookie", BrowserTransactionCookie+"=binding")
		w := httptest.NewRecorder()
		browser.ServeHTTP(w, r)
		if w.Code != 400 || authority.complete != 0 {
			t.Fatal("ambiguous response reached authority")
		}
	}
	r := browserRequest(http.MethodGet, BrowserCallbackPath+"?state=state&code=code&iss=https%3A%2F%2Fidp.example.test&scope=openid", nil)
	r.Header.Set("Cookie", BrowserTransactionCookie+"=binding")
	w := httptest.NewRecorder()
	browser.ServeHTTP(w, r)
	if w.Code != 303 || authority.complete != 1 || authority.issuer != "https://idp.example.test" {
		t.Fatal("decoded issuer not delivered for exact authority validation")
	}
}
