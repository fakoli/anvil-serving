//go:build linux

package session

import (
	"context"
	"encoding/pem"
	"errors"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/fakoli/anvil-serving/connect/internal/store"
)

// Fresh processes are necessary: Go caches system roots. The production
// constructor receives no HTTPClient override in either case. No host trust
// files are read or modified, and the only endpoint is our loopback fixture.
func TestOIDCDefaultClientRequiresSystemTrust(t *testing.T) {
	if os.Getenv("ANVIL_CONNECT_TEST_TRUST_CHILD") == "1" {
		state, err := store.Open(filepath.Join(t.TempDir(), "authority"), time.Now)
		if err != nil {
			t.Fatal("fixture state unavailable")
		}
		defer state.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		manager, err := New(ctx, state, sessionRules(), Config{
			Issuer: os.Getenv("ANVIL_CONNECT_TEST_ISSUER"), ClientID: "connect-browser",
			ClientSecret: "fixture-secret", CallbackPath: "/_connect/callback",
			TransactionLifetime: DefaultTransactionLifetime, SessionLifetime: time.Hour,
			MaxTransactions: 8, MaxPerBrowser: 2,
		})
		if os.Getenv("ANVIL_CONNECT_TEST_TRUSTED") == "1" {
			if err != nil {
				t.Fatal("OIDC discovery failed with the fixture trust anchor")
			}
			manager.Close()
		} else {
			if manager != nil {
				manager.Close()
			}
			if !errors.Is(err, ErrUnavailable) {
				t.Fatal("OIDC discovery did not fail closed without the trust anchor")
			}
		}
		return
	}
	idp := newSyntheticIDP(t)
	idp.server.Config.ErrorLog = log.New(io.Discard, "", 0)
	root := t.TempDir()
	certificate := filepath.Join(root, "fixture-ca.pem")
	if err := os.WriteFile(certificate, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: idp.server.Certificate().Raw}), 0600); err != nil {
		t.Fatal("fixture certificate unavailable")
	}
	emptyRoots := filepath.Join(root, "empty-roots")
	if err := os.Mkdir(emptyRoots, 0700); err != nil {
		t.Fatal("fixture trust directory unavailable")
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal("test executable unavailable")
	}
	for _, trusted := range []bool{false, true} {
		name, selected, expected := "untrusted", filepath.Join(root, "absent.pem"), "0"
		if trusted {
			name, selected, expected = "trusted", certificate, "1"
		}
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			command := exec.CommandContext(ctx, executable, "-test.run=^TestOIDCDefaultClientRequiresSystemTrust$", "-test.timeout=8s")
			command.Env = []string{"LANG=C", "HOME=" + root, "SSL_CERT_FILE=" + selected, "SSL_CERT_DIR=" + emptyRoots,
				"ANVIL_CONNECT_TEST_TRUST_CHILD=1", "ANVIL_CONNECT_TEST_TRUSTED=" + expected, "ANVIL_CONNECT_TEST_ISSUER=" + idp.issuer()}
			if err := command.Run(); err != nil {
				t.Fatal("isolated trust probe failed")
			}
		})
	}
}
