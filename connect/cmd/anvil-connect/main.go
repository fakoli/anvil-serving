//go:build linux

// Command anvil-connect is the native data plane. Operator automation lives in
// anvil-serving connect; this command consumes its closed generated declarations.
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

	"github.com/fakoli/anvil-serving/connect/internal/admin"
	"github.com/fakoli/anvil-serving/connect/internal/client"
	"github.com/fakoli/anvil-serving/connect/internal/clientconfig"
	"github.com/fakoli/anvil-serving/connect/internal/config"
	connectruntime "github.com/fakoli/anvil-serving/connect/internal/runtime"
	"github.com/fakoli/anvil-serving/connect/internal/transport"
)

const usage = `anvil-connect validate --mode gateway|connector|client --config FILE
anvil-connect preflight --mode gateway|connector|client --config FILE [--input GATEWAY_FILE] [--socket LOCAL_ADDRESS]
anvil-connect init --mode gateway --config FILE
anvil-connect init --mode connector --config FILE --bundle PRIVATE_FILE
anvil-connect gateway|connector|client --config FILE
anvil-connect login [--config FILE] [--json]
anvil-connect identity --config FILE
anvil-connect admin --socket PATH --request FILE [--output PRIVATE_FILE]
anvil-connect keygen --output PRIVATE_FILE
anvil-connect backup --config GATEWAY_FILE --output PRIVATE_FILE
anvil-connect restore --config GATEWAY_FILE --input PRIVATE_FILE --sha256 DIGEST
`

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	code := run(ctx, os.Args[1:], os.Stdout, os.Stderr, os.LookupEnv)
	cancel()
	os.Exit(code)
}

// No low-level error text is printed: filenames, HTTP errors and child errors
// may contain operator state. JSON status deliberately describes process state,
// never a readiness claim for an origin that has not been probed.
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
		_, err := io.WriteString(out, usage)
		if err != nil {
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
	var mode, file, bundle, socket, request, output, input, digest string
	switch command {
	case "validate", "preflight", "init":
		fs.StringVar(&mode, "mode", "", "native mode")
		fs.StringVar(&file, "config", "", "closed declaration")
		if command == "preflight" {
			fs.StringVar(&input, "input", "", "paired gateway declaration for connector trust")
			fs.StringVar(&socket, "socket", "", "wait for the declared local TLS socket")
		}
		if command == "init" {
			fs.StringVar(&bundle, "bundle", "", "private enrollment invitation")
		}
	case "gateway", "connector", "client", "login", "identity":
		fs.StringVar(&file, "config", "", "closed declaration")
		mode = command
		if command == "login" {
			mode = "client"
		}
		if command == "identity" {
			mode = "connector"
		}
	case "admin":
		fs.StringVar(&socket, "socket", "", "same-user administrative socket")
		fs.StringVar(&request, "request", "", "closed administrative request")
		fs.StringVar(&output, "output", "", "exclusive private response file")
	case "keygen":
		fs.StringVar(&output, "output", "", "exclusive private key file")
	case "backup", "restore":
		mode = "gateway"
		fs.StringVar(&file, "config", "", "closed gateway declaration")
		if command == "backup" {
			fs.StringVar(&output, "output", "", "exclusive private backup file")
		} else {
			fs.StringVar(&input, "input", "", "private backup file")
			fs.StringVar(&digest, "sha256", "", "independently retained backup digest")
		}
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
	if command == "admin" {
		data, err := readDeclaration(request)
		var input admin.Request
		if err != nil || config.Decode(bytes.NewReader(data), &input) != nil || !admin.ValidOperation(input.Operation) || socket == "" {
			return invalid()
		}
		secret := input.Operation == "invite" || input.Operation == "api-key-issue"
		if secret && output == "" {
			return invalid()
		}
		var reserved *privateOutput
		if output != "" {
			reserved, err = reserveOutput(output)
			if err != nil {
				return fail()
			}
			defer reserved.Close()
		}
		pin, err := pinPrivatePath(socket)
		if err != nil {
			return fail()
		}
		defer pin.Close()
		response, err := admin.Call(ctx, pin.Path(), input)
		if err != nil {
			return fail()
		}
		// Even an unexpected secret response cannot be emitted on stdout.
		if reserved != nil {
			data, err := json.Marshal(response)
			if err != nil || reserved.Write(append(data, '\n')) != nil {
				_ = status(map[string]any{"operation": input.Operation, "status": "output-failed", "key_id": response.KeyID, "installation": response.Installation})
				return fail()
			}
		}
		response.Secret, response.Invitation, response.InnerCAPEM = "", "", ""
		return status(response)
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
	if mode != "gateway" && mode != "connector" && mode != "client" {
		return invalid()
	}
	if command == "init" && ((mode == "gateway" && bundle != "") || (mode == "connector" && bundle == "") || mode == "client") {
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
	var gateway connectruntime.GatewayConfig
	var connector connectruntime.ConnectorConfig
	var local clientconfig.Config
	switch mode {
	case "gateway":
		gateway, err = connectruntime.ReadGateway(bytes.NewReader(data))
	case "connector":
		connector, err = connectruntime.ReadConnector(bytes.NewReader(data))
	case "client":
		local, err = clientconfig.Read(bytes.NewReader(data))
	}
	if err != nil {
		return invalid()
	}
	if command == "validate" {
		return status(map[string]string{"mode": mode, "status": "valid"})
	}
	if command == "preflight" {
		if mode != "connector" && (input != "" || socket != "") {
			return invalid()
		}
		if mode == "gateway" && gateway.VerifyLocalTunnelMaterial() != nil {
			return fail()
		}
		if mode == "connector" {
			if (connector.LocalTunnel != nil) != (input != "") {
				return invalid()
			}
			if input != "" {
				paired, readErr := readDeclaration(input)
				if readErr != nil {
					return invalid()
				}
				gateway, err = connectruntime.ReadGateway(bytes.NewReader(paired))
				if err != nil {
					return invalid()
				}
				if connector.VerifyLocalTunnelTrust(gateway) != nil {
					return fail()
				}
			}
			if socket != "" && (connector.LocalTunnel == nil || socket != connector.LocalTunnel.Address) {
				return invalid()
			}
		}
		binary := gateway.TunnelBinary
		if mode == "connector" {
			binary = connector.TunnelBinary
		}
		if mode != "client" && transport.VerifyBinary(binary) != nil {
			return fail()
		}
		if socket != "" && connector.WaitLocalTunnelEntry(ctx) != nil {
			return fail()
		}
		return status(map[string]string{"mode": mode, "status": "artifacts-verified"})
	}
	if command == "backup" {
		reserved, err := reserveOutput(output)
		if err != nil {
			return fail()
		}
		defer reserved.Close()
		data, err := connectruntime.BackupGateway(gateway)
		if err != nil || reserved.Write(data) != nil {
			return fail()
		}
		return status(map[string]string{"mode": mode, "status": "backup-created", "sha256": connectruntime.BackupDigest(data)})
	}
	if command == "restore" {
		data, err := readPrivate(input)
		if err != nil || connectruntime.RestoreGateway(gateway, data, digest) != nil {
			return fail()
		}
		return status(map[string]string{"mode": mode, "status": "restored", "grants": "disabled", "sha256": digest})
	}
	if command == "identity" {
		installation, err := connectruntime.ConnectorIdentity(connector)
		if err != nil {
			return fail()
		}
		return status(admin.InstallationStatus{ID: installation.ID, Status: installation.Status, Fingerprint: installation.Fingerprint, Epoch: installation.Epoch, Generation: installation.Generation, Resources: installation.Resources})
	}
	if command == "init" {
		if mode == "gateway" {
			err = connectruntime.InitializeGateway(gateway)
		} else {
			data, readErr := readPrivate(bundle)
			var invitation admin.Response
			if readErr != nil || config.Decode(bytes.NewReader(data), &invitation) != nil {
				return invalid()
			}
			err = connectruntime.InitializeConnector(ctx, connector, invitation)
		}
		if err != nil {
			return fail()
		}
		return status(map[string]string{"mode": mode, "status": "initialized"})
	}
	if command == "client" {
		err = serveClient(ctx, local, lookup, func() error { return json.NewEncoder(out).Encode(map[string]string{"mode": mode, "status": "running"}) })
	} else if command == "login" {
		var keyLocation loginKeyLocation
		lookup, keyLocation, err = loginSecrets(file, local, lookup)
		if err == nil {
			err = loginClient(ctx, local, lookup, out, jsonOutput, keyLocation)
		}
	} else {
		var process interface {
			Close()
			Wait(context.Context) error
		}
		if command == "gateway" {
			process, err = connectruntime.StartGateway(ctx, gateway, lookup)
		} else {
			process, err = connectruntime.StartConnector(ctx, connector, lookup)
		}
		if err != nil {
			return fail()
		}
		defer process.Close()
		if status(map[string]string{"mode": mode, "status": "running"}) != 0 {
			return 1
		}
		err = process.Wait(ctx)
		// Wait observes cancellation; Close performs and joins owned cleanup.
		// A stopped status is only true after that cleanup has completed.
		process.Close()
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
	return status(map[string]string{"mode": mode, "status": "stopped"})
}
