// Package httpedge admits requests before an origin transport sees them.
package httpedge

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/access"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/relay"
)

// Dispatch must remain active until its response or upgraded connection closes.
// It receives a credential-free request and an authenticated context separately.
type Dispatch func(http.ResponseWriter, *http.Request, config.Resource, access.Admission)

type gatewayResource struct {
	declaration config.Resource
	slots       chan struct{}
}

type Gateway struct {
	keys      *access.Keys
	resources map[string]gatewayResource
	slots     chan struct{}
	dispatch  Dispatch
}

func NewGateway(declaration config.Gateway, keys *access.Keys, dispatch Dispatch) (*Gateway, error) {
	if declaration.Validate() != nil || keys == nil || dispatch == nil {
		return nil, errors.New("invalid gateway dependencies")
	}
	g := &Gateway{keys: keys, resources: map[string]gatewayResource{}, slots: make(chan struct{}, declaration.MaxConcurrent), dispatch: dispatch}
	for _, resource := range declaration.Resources {
		resource.Rule.Methods = append([]string(nil), resource.Rule.Methods...)
		g.resources[resource.Rule.Host] = gatewayResource{declaration: resource, slots: make(chan struct{}, resource.Rule.Limits.Concurrent)}
	}
	return g, nil
}

func fail(w http.ResponseWriter, status int) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if status == http.StatusUnauthorized {
		w.Header().Set("WWW-Authenticate", `Bearer realm="anvil-connect"`)
	}
	if status == http.StatusTooManyRequests {
		w.Header().Set("Retry-After", "1")
	}
	http.Error(w, http.StatusText(status), status)
}

func acquire(slots chan struct{}) bool {
	select {
	case slots <- struct{}{}:
		return true
	default:
		return false
	}
}

func (g *Gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if ValidateHead(r) != nil {
		fail(w, http.StatusBadRequest)
		return
	}
	resource, exists := g.resources[r.Host]
	if !exists || !resource.declaration.Rule.Allows(r.Host, r.URL.Path, r.Method) {
		fail(w, http.StatusNotFound)
		return
	}
	// Browser resources remain fail closed until the session adapter is supplied.
	if resource.declaration.Rule.Access != "api" {
		fail(w, http.StatusServiceUnavailable)
		return
	}
	raw, err := APIKey(r)
	if err != nil {
		fail(w, http.StatusUnauthorized)
		return
	}
	admitted, err := g.keys.Authenticate(raw, resource.declaration.Rule.ID, r.Method)
	if err != nil {
		fail(w, http.StatusUnauthorized)
		return
	}
	if r.ContentLength > resource.declaration.Rule.Limits.RequestBytes {
		fail(w, http.StatusRequestEntityTooLarge)
		return
	}
	if !acquire(g.slots) {
		fail(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-g.slots }()
	if !acquire(resource.slots) {
		fail(w, http.StatusTooManyRequests)
		return
	}
	defer func() { <-resource.slots }()
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(resource.declaration.Rule.Limits.DurationSeconds)*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		fail(w, http.StatusRequestTimeout)
		return
	}
	clean := r.Clone(ctx)
	CleanAPIHeaders(clean.Header)
	clean.Body = relay.Body(w, http.MaxBytesReader(w, r.Body, resource.declaration.Rule.Limits.RequestBytes), ctx, time.Duration(resource.declaration.Rule.Limits.IdleSeconds)*time.Second)
	defer clean.Body.Close()
	// Close the admission gap after capacity accounting. Active revocation is a
	// separate lifetime contract; it must also cancel connections after dispatch.
	if g.keys.Check(admitted) != nil {
		fail(w, http.StatusUnauthorized)
		return
	}
	declaration := resource.declaration
	declaration.Rule.Methods = append([]string(nil), declaration.Rule.Methods...)
	g.dispatch(w, clean, declaration, admitted)
}
