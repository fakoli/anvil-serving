//go:build darwin

// Command anvil-connect on macOS provides only the separately provisioned
// local API client. Gateway, connector, administrative, and recovery roles
// retain their Linux-specific ownership and process-security contracts.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"

	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
)

const usage = `anvil-connect validate --mode client --config FILE
anvil-connect preflight --mode client --config FILE
anvil-connect client --config FILE
anvil-connect login [--config FILE] [--json]
anvil-connect keygen --output PRIVATE_FILE
`

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	code := run(ctx, os.Args[1:], os.Stdout, os.Stderr, os.LookupEnv)
	cancel()
	os.Exit(code)
}

// No low-level error text is printed: filenames and HTTP errors may contain
// operator state. Unsupported roles fail closed rather than attempting a
// weaker macOS version of the Linux gateway or connector runtime.
func run(ctx context.Context, args []string, out, diagnostics io.Writer, lookup func(string) (string, bool)) int {
	fail := func() int {
		_, _ = fmt.Fprintln(diagnostics, "anvil-connect: operation failed; inspect managed status")
		return 1
	}
	invalid := func() int {
		_, _ = fmt.Fprintln(diagnostics, "anvil-connect: invalid command or declaration")
		return 2
	}
	if len(args) == 0 || (len(args) == 1 && (args[0] == "help" || args[0] == "--help" || args[0] == "-h")) {
		if _, err := io.WriteString(out, usage); err != nil {
			return fail()
		}
		return 0
	}
	if ctx == nil || ctx.Err() != nil || lookup == nil {
		return invalid()
	}

	command := args[0]
	fs := flag.NewFlagSet(command, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	var jsonOutput bool
	if command == "login" {
		fs.BoolVar(&jsonOutput, "json", false, "machine-readable login output")
	}
	var mode, file, output string
	switch command {
	case "validate", "preflight":
		fs.StringVar(&mode, "mode", "", "native mode")
		fs.StringVar(&file, "config", "", "closed declaration")
	case "client", "login":
		mode = "client"
		fs.StringVar(&file, "config", "", "closed declaration")
	case "keygen":
		fs.StringVar(&output, "output", "", "exclusive private key file")
	default:
		return invalid()
	}
	if fs.Parse(args[1:]) != nil || fs.NArg() != 0 {
		return invalid()
	}
	status := func(value any) int {
		if json.NewEncoder(out).Encode(value) != nil {
			return fail()
		}
		return 0
	}
	if command == "keygen" {
		reserved, err := reserveOutput(output)
		if err != nil {
			return fail()
		}
		defer reserved.Close()
		key, err := client.GenerateKey()
		if err != nil || reserved.Write([]byte(key+"\n")) != nil {
			return fail()
		}
		return status(map[string]string{"mode": "client", "status": "key-created"})
	}
	if mode != "client" {
		return invalid()
	}
	if command == "login" {
		explicitConfig := false
		fs.Visit(func(f *flag.Flag) {
			if f.Name == "config" {
				explicitConfig = true
			}
		})
		if explicitConfig && file == "" {
			return invalid()
		}
		home, _ := os.UserHomeDir()
		selected, pathErr := loginConfigPath(file, home)
		if pathErr != nil {
			return invalid()
		}
		file = selected
	}
	data, err := readDeclaration(file)
	if err != nil {
		if command == "login" {
			_, _ = fmt.Fprintln(diagnostics, "anvil-connect: client setup unavailable; install the client configuration or use --config FILE")
			return 2
		}
		return invalid()
	}
	local, err := clientconfig.Read(bytes.NewReader(data))
	if err != nil {
		return invalid()
	}
	switch command {
	case "validate":
		return status(map[string]string{"mode": "client", "status": "valid"})
	case "preflight":
		// A portable client owns no tunnel binary or gateway state. Its closed
		// declaration is the complete local preflight artifact.
		return status(map[string]string{"mode": "client", "status": "artifacts-verified"})
	case "client":
		err = serveClient(ctx, local, lookup, func() error {
			return json.NewEncoder(out).Encode(map[string]string{"mode": "client", "status": "running"})
		})
	case "login":
		var keyLocation loginKeyLocation
		lookup, keyLocation, err = loginSecrets(file, local, lookup)
		if err == nil {
			err = loginClient(ctx, local, lookup, out, jsonOutput, keyLocation)
		}
	default:
		return invalid()
	}
	if err != nil {
		if command == "login" {
			return loginFailure(diagnostics, err)
		}
		return fail()
	}
	if command == "login" && !jsonOutput {
		_, _ = fmt.Fprintln(out, "Connect stopped.")
		return 0
	}
	return status(map[string]string{"mode": "client", "status": "stopped"})
}
