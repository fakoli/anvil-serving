package session

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/store"
	"golang.org/x/oauth2"
)

const transactionPrefix = "tx:"

const (
	maxTransactionsStored = 128
	oidcTimeout           = 5 * time.Second
	maxOIDCResponseBytes  = 1024 * 1024
	maxOIDCHeaderBytes    = 64 * 1024
)

func transactionKey(state string) string {
	digest := sha256.Sum256([]byte(state))
	return transactionPrefix + hex.EncodeToString(digest[:])
}

func validatedIssuer(value string) (*url.URL, error) {
	u, err := url.Parse(value)
	if err != nil || !validOIDCURL(u, value) {
		return nil, ErrConfiguration
	}
	return u, nil
}

func sameIssuerOrigin(issuer *url.URL, value string) bool {
	u, err := url.Parse(value)
	return err == nil && validOIDCURL(u, value) && u.Scheme == issuer.Scheme && u.Host == issuer.Host && u.Path != ""
}

func validOIDCURL(u *url.URL, raw string) bool {
	return u != nil && u.Scheme == "https" && u.Host != "" && u.User == nil && u.RawQuery == "" && !u.ForceQuery && u.Fragment == "" && u.RawPath == "" && (u.Path == "" || config.CanonicalPath(u.Path)) && !strings.ContainsAny(raw, "\\\r\n\t #?")
}

type boundedTransport struct {
	base   http.RoundTripper
	issuer *url.URL
}

func (t boundedTransport) CloseIdleConnections() {
	if closer, ok := t.base.(interface{ CloseIdleConnections() }); ok {
		closer.CloseIdleConnections()
	}
}

func (t boundedTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	if request.URL == nil || request.URL.Scheme != "https" || request.URL.Scheme != t.issuer.Scheme || request.URL.Host != t.issuer.Host || request.URL.User != nil {
		return nil, ErrUnavailable
	}
	response, err := t.base.RoundTrip(request)
	if err != nil {
		return nil, err
	}
	var headerBytes int
	for name, values := range response.Header {
		for _, value := range values {
			headerBytes += len(name) + len(value) + 4
		}
	}
	if headerBytes > maxOIDCHeaderBytes {
		response.Body.Close()
		return nil, ErrUnavailable
	}
	response.Body = &limitedBody{body: response.Body, remaining: maxOIDCResponseBytes}
	return response, nil
}

type limitedBody struct {
	body      io.ReadCloser
	remaining int64
}

func (b *limitedBody) Read(data []byte) (int, error) {
	if b.remaining == 0 {
		var probe [1]byte
		n, err := b.body.Read(probe[:])
		if n > 0 {
			return 0, ErrUnavailable
		}
		return 0, err
	}
	if int64(len(data)) > b.remaining {
		data = data[:b.remaining]
	}
	n, err := b.body.Read(data)
	b.remaining -= int64(n)
	return n, err
}

func (b *limitedBody) Close() error { return b.body.Close() }

func ownedOIDCClient(input *http.Client, issuer *url.URL) (*http.Client, error) {
	var transport *http.Transport
	if input == nil || input.Transport == nil {
		transport = http.DefaultTransport.(*http.Transport)
	} else {
		var ok bool
		transport, ok = input.Transport.(*http.Transport)
		if !ok {
			return nil, ErrConfiguration
		}
	}
	if transport.DialTLS != nil || transport.DialTLSContext != nil || (transport.TLSClientConfig != nil && transport.TLSClientConfig.InsecureSkipVerify) {
		return nil, ErrConfiguration
	}
	clone := transport.Clone()
	clone.Proxy = nil
	clone.TLSNextProto = nil
	clone.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS13, ServerName: issuer.Hostname()}
	if transport.TLSClientConfig != nil && transport.TLSClientConfig.RootCAs != nil {
		clone.TLSClientConfig.RootCAs = transport.TLSClientConfig.RootCAs.Clone()
	}
	clone.ResponseHeaderTimeout = oidcTimeout
	clone.TLSHandshakeTimeout = oidcTimeout
	clone.MaxResponseHeaderBytes = maxOIDCHeaderBytes
	return &http.Client{Transport: boundedTransport{base: clone, issuer: issuer}, Timeout: oidcTimeout, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}, nil
}

func (m *Manager) putTransaction(record transaction) error {
	return m.state.Update(func(tx *store.Tx) error {
		record.IssuedAt = tx.Now()
		record.ExpiresAt = record.IssuedAt.Add(m.transactionLifetime)
		record.Epoch = tx.Epoch()
		records, err := tx.List("transactions", transactionPrefix, maxTransactionsStored)
		if err != nil {
			return ErrUnavailable
		}
		active, forBrowser := 0, 0
		for _, item := range records {
			var existing transaction
			if json.Unmarshal(item.Value, &existing) != nil {
				return ErrUnavailable
			}
			if existing.Epoch != tx.Epoch() || tx.Now().Before(existing.IssuedAt) || !tx.Now().Before(existing.ExpiresAt) {
				if tx.Delete("transactions", item.ID) != nil {
					return ErrUnavailable
				}
				continue
			}
			active++
			if existing.Binding == record.Binding {
				forBrowser++
			}
		}
		if active >= m.maxTransactions || forBrowser >= m.maxPerBrowser {
			return ErrDenied
		}
		// No random state is stored in clear. A collision is cryptographically
		// implausible and still rejected rather than overwriting another flow.
		key := transactionPrefix + hex.EncodeToString(record.State[:])
		var old transaction
		if err := tx.Get("transactions", key, &old); !errors.Is(err, store.ErrMissing) {
			return ErrUnavailable
		}
		return tx.Put("transactions", key, record)
	})
}

// Complete consumes the transaction before the token exchange, so a callback
// code can never be replayed concurrently. A binding mismatch does not consume
// it because the legitimate browser still possesses the binding cookie.
func (m *Manager) Complete(ctx context.Context, callback Callback) (Completion, error) {
	if callback.State == "" || len(callback.State) > 256 || callback.Code == "" || len(callback.Code) > 8192 || !validBinding(callback.Binding) {
		return Completion{}, ErrDenied
	}
	// RFC 9207: compare the decoded response issuer exactly before consuming
	// state or sending the code to the token endpoint. A provider advertising
	// support must always return it; legacy single-issuer providers may omit it.
	if (callback.Issuer != "" && callback.Issuer != m.issuer) || (m.responseIssuerRequired && callback.Issuer == "") {
		return Completion{}, ErrDenied
	}
	key := transactionKey(callback.State)
	stateHash := sha256.Sum256([]byte(callback.State))
	bindingHash := sha256.Sum256([]byte(callback.Binding))
	var record transaction
	var transactionNow time.Time
	if err := m.state.View(func(tx *store.Tx) error {
		transactionNow = tx.Now()
		if err := tx.Get("transactions", key, &record); err != nil || record.Epoch != tx.Epoch() || transactionNow.Before(record.IssuedAt) || !transactionNow.Before(record.ExpiresAt) || record.Host != callback.Host || subtle.ConstantTimeCompare(record.State[:], stateHash[:]) != 1 || subtle.ConstantTimeCompare(record.Binding[:], bindingHash[:]) != 1 {
			return ErrDenied
		}
		return nil
	}); err != nil {
		return Completion{}, ErrDenied
	}
	if err := m.state.Update(func(tx *store.Tx) error {
		var current transaction
		if err := tx.Get("transactions", key, &current); err != nil || current.Epoch != tx.Epoch() || tx.Now().Before(current.IssuedAt) || !tx.Now().Before(current.ExpiresAt) || current != record {
			return ErrDenied
		}
		return tx.Delete("transactions", key)
	}); err != nil {
		return Completion{}, ErrDenied
	}
	ctx = oidc.ClientContext(ctx, m.client)
	ctx = context.WithValue(ctx, oauth2.HTTPClient, m.client)
	oauth := m.oauthConfig(record.RedirectURL)
	token, err := oauth.Exchange(ctx, callback.Code, oauth2.VerifierOption(record.PKCEVerifier))
	if err != nil {
		return Completion{}, ErrDenied
	}
	rawIDToken, ok := token.Extra("id_token").(string)
	if !ok || rawIDToken == "" {
		return Completion{}, ErrDenied
	}
	var verifiedNow time.Time
	if err := m.state.View(func(tx *store.Tx) error {
		verifiedNow = tx.Now()
		return nil
	}); err != nil {
		return Completion{}, ErrUnavailable
	}
	verifier := m.provider.Verifier(&oidc.Config{ClientID: m.clientID, Now: func() time.Time { return verifiedNow }})
	idToken, err := verifier.Verify(ctx, rawIDToken)
	if err != nil || !validIDToken(idToken, verifiedNow, m.issuer, m.clientID) || idToken.Subject == "" || idToken.Nonce == "" || sha256.Sum256([]byte(idToken.Nonce)) != record.Nonce {
		return Completion{}, ErrDenied
	}
	principal, ok := humanID(m.issuer, idToken.Subject)
	if !ok {
		return Completion{}, ErrDenied
	}
	return m.issueSession(principal, record, idToken.Expiry)
}

func validIDToken(token *oidc.IDToken, now time.Time, issuer, clientID string) bool {
	if token == nil || token.Issuer != issuer || len(token.Audience) != 1 || token.Audience[0] != clientID || token.IssuedAt.IsZero() || !token.IssuedAt.Before(token.Expiry) || !now.Before(token.Expiry) || token.IssuedAt.After(now) {
		return false
	}
	var extra struct {
		AuthorizedParty string `json:"azp"`
		NotBefore       *int64 `json:"nbf"`
	}
	if token.Claims(&extra) != nil || (extra.AuthorizedParty != "" && extra.AuthorizedParty != clientID) {
		return false
	}
	return extra.NotBefore == nil || time.Unix(*extra.NotBefore, 0).UTC().Compare(now) <= 0
}

func (m *Manager) issueSession(principal string, record transaction, tokenExpiry time.Time) (Completion, error) {
	random, err := randomURLValue(48)
	if err != nil {
		return Completion{}, err
	}
	// 48 URL bytes decode to a 64-character value. Derive the fixed-format
	// 16-byte public id and 32-byte private component from the random bytes.
	decoded, err := base64.RawURLEncoding.DecodeString(random)
	if err != nil || len(decoded) != 48 {
		return Completion{}, ErrUnavailable
	}
	id := hex.EncodeToString(decoded[:16])
	secret := base64.RawURLEncoding.EncodeToString(decoded[16:])
	raw := "acs1." + id + "." + secret
	result := Completion{Cookie: raw, Host: record.Host, Resource: record.Resource}
	err = m.state.Update(func(tx *store.Tx) error {
		now := tx.Now()
		if record.Epoch != tx.Epoch() || now.Before(record.IssuedAt) || !now.Before(record.ExpiresAt) || !now.Before(tokenExpiry) {
			return ErrDenied
		}
		var human Human
		if tx.Get("principals", principal, &human) != nil || human.Disabled || human.Generation == 0 || !hasResource(human.Resources, record.Resource) {
			return ErrDenied
		}
		if err := m.boundSessions(tx, principal); err != nil {
			return err
		}
		var existing Session
		if err := tx.Get("sessions", sessionRecord(id), &existing); !errors.Is(err, store.ErrMissing) {
			return ErrUnavailable
		}
		session := Session{ID: id, Digest: hashValue(raw), Principal: principal, PrincipalGeneration: human.Generation, Resource: record.Resource, Host: record.Host, Generation: 1, Epoch: tx.Epoch(), IssuedAt: now, ExpiresAt: now.Add(m.sessionLifetime)}
		result.ExpiresAt, result.ReturnPath = session.ExpiresAt, record.ReturnPath
		return tx.Put("sessions", sessionRecord(id), session)
	})
	if err != nil {
		return Completion{}, err
	}
	return result, nil
}
