package session

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/store"
	"github.com/fakoli/anvil-serving/connect/internal/testpki"
)

func TestOIDCTransportCannotSkipTLSOrSelectAnotherPeer(t *testing.T) {
	issuer, _ := url.Parse("https://auth.example.test")
	for _, kind := range []string{"dial-tls", "dial-tls-context"} {
		t.Run(kind, func(t *testing.T) {
			tr := &http.Transport{}
			if kind == "dial-tls" {
				tr.DialTLS = func(string, string) (net.Conn, error) { t.Fatal("custom TLS dialer called"); return nil, nil }
			} else {
				tr.DialTLSContext = func(context.Context, string, string) (net.Conn, error) {
					t.Fatal("custom TLS dialer called")
					return nil, nil
				}
			}
			if client, err := ownedOIDCClient(&http.Client{Transport: tr}, issuer); err == nil {
				client.CloseIdleConnections()
				t.Fatal("custom TLS authentication bypass accepted")
			}
		})
	}
	ca := testpki.New(t)
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("different TLS identity reached IdP handler") }))
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{ca.Leaf(t, "other.example.test", false)}}
	server.StartTLS()
	defer server.Close()
	tr := &http.Transport{TLSClientConfig: &tls.Config{RootCAs: ca.Roots, ServerName: "other.example.test"}, DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp4", server.Listener.Addr().String())
	}}
	client, err := ownedOIDCClient(&http.Client{Transport: tr}, issuer)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseIdleConnections()
	response, err := client.Get(issuer.String() + "/keys")
	if response != nil {
		response.Body.Close()
	}
	if err == nil {
		t.Fatal("preset ServerName authenticated another peer")
	}
}

func callbackRecord(t *testing.T, state *store.Store, now time.Time) transaction {
	t.Helper()
	record := transaction{Resource: "dash", Host: "dash.example.test", ReturnPath: "/", IssuedAt: now, ExpiresAt: now.Add(time.Minute)}
	if err := state.View(func(tx *store.Tx) error { record.Epoch = tx.Epoch(); return nil }); err != nil {
		t.Fatal(err)
	}
	return record
}

func TestSessionCapsAndStaleRecordReclamation(t *testing.T) {
	m, state, idp, now, _ := sessionFixture(t, 8, 2)
	defer m.Close()
	human, err := m.SetHuman(idp.issuer(), "bounded-human", []string{"dash"}, false)
	if err != nil {
		t.Fatal(err)
	}
	record := callbackRecord(t, state, *now)
	var first Completion
	for i := 0; i < MaximumSessionsPerHuman; i++ {
		result, err := m.issueSession(human.ID, record, now.Add(time.Hour))
		if err != nil {
			t.Fatal(err)
		}
		if i == 0 {
			first = result
		}
	}
	if _, err := m.issueSession(human.ID, record, now.Add(time.Hour)); err == nil {
		t.Fatal("per-human session bound exceeded")
	}
	if err := m.Logout(first.Cookie, record.Host); err != nil {
		t.Fatal(err)
	}
	if _, err := m.issueSession(human.ID, record, now.Add(time.Hour)); err != nil {
		t.Fatal("stale generation sessions not reclaimed", err)
	}
	assertCount := func(want int) {
		t.Helper()
		if err := state.View(func(tx *store.Tx) error {
			items, err := tx.List("sessions", sessionPrefix, MaximumSessions)
			if err != nil {
				return err
			}
			if len(items) != want {
				t.Fatal("session records accumulated", len(items))
			}
			return nil
		}); err != nil {
			t.Fatal(err)
		}
	}
	assertCount(1)
	*now = now.Add(time.Hour)
	record = callbackRecord(t, state, *now)
	if _, err := m.issueSession(human.ID, record, now.Add(time.Hour)); err != nil {
		t.Fatal(err)
	}
	assertCount(1)
	if err := state.ResetAuthority(); err != nil {
		t.Fatal(err)
	}
	record = callbackRecord(t, state, *now)
	if _, err := m.issueSession(human.ID, record, now.Add(time.Hour)); err != nil {
		t.Fatal(err)
	}
	assertCount(1)
}

func TestGlobalSessionBoundAcrossHumans(t *testing.T) {
	m, state, idp, now, _ := sessionFixture(t, 8, 2)
	defer m.Close()
	var humans []Human
	for i := 0; i < MaximumSessions/MaximumSessionsPerHuman+1; i++ {
		human, err := m.SetHuman(idp.issuer(), fmt.Sprintf("person-%d", i), []string{"dash"}, false)
		if err != nil {
			t.Fatal(err)
		}
		humans = append(humans, human)
	}
	record := callbackRecord(t, state, *now)
	if err := state.Update(func(tx *store.Tx) error {
		for i := 0; i < MaximumSessions; i++ {
			human := humans[i/MaximumSessionsPerHuman]
			id := fmt.Sprintf("%032x", i)
			s := Session{ID: id, Principal: human.ID, PrincipalGeneration: human.Generation, Generation: 1, Resource: "dash", Host: record.Host, Epoch: tx.Epoch(), IssuedAt: tx.Now(), ExpiresAt: tx.Now().Add(time.Hour)}
			if err := tx.Put("sessions", sessionRecord(id), s); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := m.issueSession(humans[len(humans)-1].ID, record, now.Add(time.Hour)); err == nil {
		t.Fatal("global session bound exceeded")
	}
	if err := state.Update(func(tx *store.Tx) error {
		id := fmt.Sprintf("%032x", 0)
		var s Session
		if err := tx.Get("sessions", sessionRecord(id), &s); err != nil {
			return err
		}
		s.Revoked = true
		return tx.Put("sessions", sessionRecord(id), s)
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := m.issueSession(humans[len(humans)-1].ID, record, now.Add(time.Hour)); err != nil {
		t.Fatal("revoked session slot not reclaimed", err)
	}
}
