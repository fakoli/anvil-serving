package control

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"net"
	"sort"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/identity"
)

// Enroll consumes one owner-issued invitation using a locally generated
// installation key. Metadata is an owner-delivered binding: it fixes the
// installation identity, role, epoch, generation and resource set before any
// network response is accepted.
func Enroll(ctx context.Context, gatewayURL, rawInvitation string, metadata identity.Invitation, private ed25519.PrivateKey, options ClientOptions) (identity.Installation, error) {
	return enroll(ctx, gatewayURL, rawInvitation, metadata, private, options, nil)
}

func enroll(ctx context.Context, gatewayURL, rawInvitation string, metadata identity.Invitation, private ed25519.PrivateKey, options ClientOptions, dial func(context.Context, string, string) (net.Conn, error)) (identity.Installation, error) {
	if ctx == nil || ctx.Err() != nil || rawInvitation == "" || !validInvitation(metadata) || len(private) != ed25519.PrivateKeySize {
		return identity.Installation{}, ErrClient
	}
	public, fingerprint, err := identity.PublicKey(private)
	if err != nil {
		return identity.Installation{}, ErrClient
	}
	provisional := identity.Installation{ID: metadata.Installation, Role: metadata.Role, Resources: append([]string(nil), metadata.Resources...), PublicKey: append(json.RawMessage(nil), public...), Fingerprint: fingerprint, Generation: metadata.Generation, Epoch: metadata.Epoch, Status: "pending"}
	client, err := newClient(gatewayURL, provisional, private, options, dial)
	if err != nil {
		return identity.Installation{}, ErrClient
	}
	defer client.Close()
	claims, err := identity.NewAssertion(metadata.Installation, gatewayURL+"/enroll", "connector", "", metadata.Epoch, identity.EnrollmentNonce(rawInvitation), 0, time.Now())
	if err != nil {
		return identity.Installation{}, ErrClient
	}
	proof, err := identity.Sign(private, claims)
	if err != nil {
		return identity.Installation{}, ErrClient
	}
	var response InstallationResponse
	if client.post(ctx, EnrollmentPath, EnrollmentRequest{Invitation: rawInvitation, PublicKey: encoded(public), Proof: proof}, &response) != nil || response.Schema != Schema || response.Installation != metadata.Installation || response.Role != metadata.Role || response.Status != "pending" || response.Epoch != metadata.Epoch || response.Generation != metadata.Generation || response.Fingerprint != fingerprint {
		return identity.Installation{}, ErrClient
	}
	return provisional, nil
}

func validInvitation(metadata identity.Invitation) bool {
	if !config.ValidID(metadata.Installation) || metadata.Role != "connector" || metadata.Generation == 0 || len(metadata.Epoch) != 64 {
		return false
	}
	resources := append([]string(nil), metadata.Resources...)
	if len(resources) < 1 || len(resources) > 64 {
		return false
	}
	sort.Strings(resources)
	for index, resource := range resources {
		if !config.ValidID(resource) || (index != 0 && resource == resources[index-1]) {
			return false
		}
	}
	return true
}
