package ingresshttp

import (
	"errors"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

func validPolicy() Policy {
	return Policy{GatewayUID: 1000, EdgeUID: 1001, GroupID: 1002, Directory: "/run/anvil-connect/ingress"}
}

func TestPolicyValidateRejectsUnsafeDeclarations(t *testing.T) {
	if err := validPolicy().Validate(); err != nil {
		t.Fatalf("valid policy: %v", err)
	}
	for name, mutate := range map[string]func(*Policy){
		"gateway-root":  func(p *Policy) { p.GatewayUID = 0 },
		"edge-root":     func(p *Policy) { p.EdgeUID = 0 },
		"group-root":    func(p *Policy) { p.GroupID = 0 },
		"gateway-large": func(p *Policy) { p.GatewayUID = maximumID + 1 },
		"edge-large":    func(p *Policy) { p.EdgeUID = maximumID + 1 },
		"group-large":   func(p *Policy) { p.GroupID = maximumID + 1 },
		"same-uid":      func(p *Policy) { p.EdgeUID = p.GatewayUID },
		"relative":      func(p *Policy) { p.Directory = "run/ingress" },
		"root":          func(p *Policy) { p.Directory = "/" },
		"unclean":       func(p *Policy) { p.Directory = "/run/ingress/../other" },
		"control":       func(p *Policy) { p.Directory = "/run/ingress\nother" },
	} {
		t.Run(name, func(t *testing.T) {
			policy := validPolicy()
			mutate(&policy)
			if !errors.Is(policy.Validate(), ErrIngress) {
				t.Fatal("unsafe policy accepted")
			}
		})
	}
}

func TestListenRejectsMismatchedGatewayUIDBeforeFilesystemAccess(t *testing.T) {
	policy := validPolicy()
	policy.GatewayUID = uint32(os.Geteuid()) + 1
	if policy.GatewayUID == policy.EdgeUID {
		policy.EdgeUID++
	}
	if listener, err := Listen(policy); !errors.Is(err, ErrIngress) || listener != nil {
		t.Fatalf("mismatched gateway listener = %v, %v", listener, err)
	}
}

func TestListenRejectsUnsafeAncestorAndSymlinkPath(t *testing.T) {
	root := t.TempDir()
	leaf := filepath.Join(root, "ingress")
	if err := os.Mkdir(leaf, 02710); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(leaf, os.ModeSetgid|0710); err != nil {
		t.Fatal(err)
	}
	policy := Policy{GatewayUID: uint32(os.Geteuid()), EdgeUID: uint32(os.Geteuid()) + 1, GroupID: uint32(os.Getegid()), Directory: leaf}
	if policy.EdgeUID == policy.GatewayUID {
		t.Skip("cannot choose a distinct UID on this platform")
	}
	if listener, err := Listen(policy); !errors.Is(err, ErrIngress) || listener != nil {
		t.Fatalf("unsafe ancestor listener = %v, %v", listener, err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(leaf, link); err != nil {
		t.Fatal(err)
	}
	policy.Directory = link
	if listener, err := Listen(policy); !errors.Is(err, ErrIngress) || listener != nil {
		t.Fatalf("symlink listener = %v, %v", listener, err)
	}
}

func TestPeerUIDUsesLinuxCredentials(t *testing.T) {
	path := filepath.Join(t.TempDir(), socketName)
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	listener.SetUnlinkOnClose(false)
	defer func() { _ = listener.Close(); _ = os.Remove(path) }()
	accepted := make(chan bool, 1)
	go func() {
		connection, err := listener.AcceptUnix()
		if err != nil {
			accepted <- false
			return
		}
		defer connection.Close()
		accepted <- peerUID(connection, uint32(os.Geteuid())) && !peerUID(connection, uint32(os.Geteuid())+1)
	}()
	client, err := net.DialTimeout("unix", path, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if !<-accepted {
		t.Fatal("same-UID Unix peer was not recognized")
	}
}

func TestRemoveMatchingPreservesReplacement(t *testing.T) {
	directory := t.TempDir()
	fd, err := unix.Open(directory, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer unix.Close(fd)
	path := filepath.Join(directory, socketName)
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	listener.SetUnlinkOnClose(false)
	defer listener.Close()
	original, err := ownedSocketStat(fd, Policy{GatewayUID: uint32(os.Geteuid())})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ownedSocketStat(fd, Policy{GatewayUID: uint32(os.Geteuid()) + 1}); !errors.Is(err, ErrIngress) {
		t.Fatalf("foreign socket identity error = %v", err)
	}
	moved := path + ".moved"
	if err := os.Rename(path, moved); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	removeMatching(fd, original)
	contents, err := os.ReadFile(path)
	if err != nil || string(contents) != "replacement" {
		t.Fatalf("replacement preserved = %q, %v", contents, err)
	}
	_ = os.Remove(moved)
}

func TestPinnedSocketPathBounded(t *testing.T) {
	if _, err := pinnedSocketPath(-1); !errors.Is(err, ErrIngress) {
		t.Fatalf("pinnedSocketPath(-1) error = %v", err)
	}
	path, err := pinnedSocketPath(7)
	if err != nil || path != "/proc/self/fd/7/"+socketName {
		t.Fatalf("pinnedSocketPath(7) = %q, %v", path, err)
	}
	if strings.Contains(socketName, "/") {
		t.Fatal("socket name must not traverse")
	}
}

func TestDirectoryStatRulesAreExact(t *testing.T) {
	policy := validPolicy()
	ancestor := unix.Stat_t{Mode: unix.S_IFDIR | 0755, Uid: 0}
	if !validAncestor(&ancestor) {
		t.Fatal("root-owned non-writable ancestor was rejected")
	}
	ancestor.Mode = unix.S_IFDIR | 0775
	if validAncestor(&ancestor) {
		t.Fatal("group-writable ancestor was accepted")
	}
	leaf := unix.Stat_t{Mode: unix.S_IFDIR | 02710, Uid: policy.GatewayUID, Gid: policy.GroupID}
	if !validLeaf(&leaf, policy) {
		t.Fatal("exact ingress directory was rejected")
	}
	leaf.Mode = unix.S_IFDIR | 02750
	if validLeaf(&leaf, policy) {
		t.Fatal("over-permissive ingress directory was accepted")
	}
}
