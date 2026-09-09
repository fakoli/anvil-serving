package runtime

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"

	"github.com/fakoli/anvil-serving/connect/internal/config"
	"github.com/fakoli/anvil-serving/connect/internal/privatefiles"
	"github.com/fakoli/anvil-serving/connect/internal/store"
)

const MaxBackupBytes = 1024 * 1024

// No paths are accepted from the archive. Operator configuration, Authelia
// state/secrets and deployment binaries have independent restore procedures.
type backup struct {
	Schema      string `json:"schema"`
	ControlHost string `json:"control_host"`
	TunnelHost  string `json:"tunnel_host"`
	Authorities string `json:"authorities"`
	State       string `json:"state"`
}

func BackupDigest(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

// BackupGateway requires the gateway to be stopped: opening its database takes
// the same exclusive lock as the gateway, with a bounded timeout. It never
// initializes an absent database or captures live ephemeral service credentials.
func BackupGateway(declaration GatewayConfig) ([]byte, error) {
	if declaration.Validate() != nil {
		return nil, ErrConfiguration
	}
	info, err := os.Lstat(declaration.StateDirectory)
	if err != nil || !info.IsDir() {
		return nil, ErrUnavailable
	}
	directory, err := privatefiles.OpenExisting(declaration.StateDirectory)
	if err != nil {
		return nil, ErrUnavailable
	}
	defer directory.Close()
	if _, _, err := loadAuthorities(directory); err != nil {
		return nil, ErrUnavailable
	}
	ca, err := directory.Read("authorities.json", 32768)
	if err != nil {
		return nil, ErrUnavailable
	}
	path, err := directory.PinPath("authority")
	if err != nil {
		return nil, ErrUnavailable
	}
	defer path.Close()
	info, err = os.Lstat(filepath.Join(path.Path(), "state.db"))
	if err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
		return nil, ErrUnavailable
	}
	state, err := store.OpenExisting(path.Path(), nil)
	if err != nil {
		return nil, ErrUnavailable
	}
	snapshot, snapshotErr := state.Snapshot()
	closeErr := state.Close()
	if snapshotErr != nil || closeErr != nil {
		return nil, ErrUnavailable
	}
	data, err := json.Marshal(backup{"anvil-connect.gateway-backup/v1", declaration.ControlHost, declaration.TunnelHost, string(ca), string(snapshot)})
	if err != nil || len(data) > MaxBackupBytes {
		return nil, ErrUnavailable
	}
	return data, nil
}

// RestoreGateway requires a fresh destination and a SHA-256 recorded separately
// at backup time. The digest is a trusted operator input, not a checksum accepted
// from inside the archive. It detects substitution only while that independent
// value remains trusted; it is not an encrypted backup or a signed provenance.
//
// Restored state is fenced before authorities.json is published last. A failed
// restore leaves an inert private directory for inspection and refuses reuse.
// No existing deployment is removed, stopped, replaced, or rolled back here.
func RestoreGateway(declaration GatewayConfig, data []byte, expectedDigest string) error {
	return restoreGateway(declaration, data, expectedDigest, syncRecoveryDirectory)
}

func syncRecoveryDirectory(path string) error {
	file, err := os.Open(path)
	if err != nil {
		return ErrUnavailable
	}
	err = file.Sync()
	closeErr := file.Close()
	if err != nil || closeErr != nil {
		return ErrUnavailable
	}
	return nil
}

func restoreGateway(declaration GatewayConfig, data []byte, expectedDigest string, syncDirectory func(string) error) error {
	decoded, err := hex.DecodeString(expectedDigest)
	if declaration.Validate() != nil || len(data) > MaxBackupBytes || err != nil || len(decoded) != 32 || hex.EncodeToString(decoded) != expectedDigest || subtle.ConstantTimeCompare([]byte(BackupDigest(data)), []byte(expectedDigest)) != 1 {
		return ErrConfiguration
	}
	var archive backup
	if config.Decode(bytes.NewReader(data), &archive) != nil || archive.Schema != "anvil-connect.gateway-backup/v1" || archive.ControlHost != declaration.ControlHost || archive.TunnelHost != declaration.TunnelHost || store.ValidateSnapshot([]byte(archive.State)) != nil {
		return ErrConfiguration
	}
	var ca authorities
	if config.Decode(bytes.NewReader([]byte(archive.Authorities)), &ca) != nil || ca.Schema != "anvil-connect.authorities/v1" {
		return ErrConfiguration
	}
	inner, err := parseCA(ca.Inner)
	if err != nil {
		return ErrConfiguration
	}
	tunnel, err := parseCA(ca.Tunnel)
	if err != nil || bytes.Equal(inner.certificate.RawSubjectPublicKeyInfo, tunnel.certificate.RawSubjectPublicKeyInfo) {
		return ErrConfiguration
	}
	// Mkdir, rather than MkdirAll/Open, reserves a new destination exclusively.
	// The parent must already exist and be owned by this service identity.
	parent, err := privatefiles.OpenExisting(filepath.Dir(declaration.StateDirectory))
	if err != nil {
		return ErrUnavailable
	}
	defer parent.Close()
	destination, err := parent.PinPath(filepath.Base(declaration.StateDirectory))
	if err != nil {
		return ErrUnavailable
	}
	defer destination.Close()
	if os.Mkdir(destination.Path(), 0700) != nil {
		return ErrUnavailable
	}
	parentFD, err := os.Open(filepath.Dir(destination.Path()))
	if err != nil {
		return ErrUnavailable
	}
	syncErr := parentFD.Sync()
	closeParentErr := parentFD.Close()
	if syncErr != nil || closeParentErr != nil {
		return ErrUnavailable
	}
	directory, err := privatefiles.Open(destination.Path())
	if err != nil {
		return ErrUnavailable
	}
	defer directory.Close()
	statePath, err := directory.PinPath("authority")
	if err != nil {
		return ErrUnavailable
	}
	defer statePath.Close()
	state, err := store.Open(statePath.Path(), nil)
	if err != nil {
		return ErrUnavailable
	}
	restoreErr := state.RestoreSnapshot([]byte(archive.State))
	closeErr := state.Close()
	if restoreErr != nil || closeErr != nil {
		return ErrUnavailable
	}
	if syncDirectory(statePath.Path()) != nil {
		return ErrUnavailable
	}
	// Both payload and receipt are private, durable, exclusive files. Publishing
	// the CA file is the only operation that makes StartGateway possible.
	if directory.Create("recovery.json", []byte(`{"schema":"anvil-connect.recovery/v1","backup_sha256":"`+expectedDigest+`","grants":"disabled"}`)) != nil || directory.Create("authorities.json", []byte(archive.Authorities)) != nil {
		return ErrUnavailable
	}
	return nil
}
