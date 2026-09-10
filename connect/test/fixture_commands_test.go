package browserfixture

import (
	"bufio"
	"errors"
	"io"
	"strings"
	"testing"
)

const fixtureCommandMaxBytes = 256

var errFixtureCommandInput = errors.New("fixture command input denied")

type fixtureCommandResult struct {
	command string
	err     error
}

// fixtureCommands preserves newline framing across arbitrary stdin reads. It
// never forwards an unterminated or oversized line to a fixture command loop.
func fixtureCommands(input io.Reader) <-chan fixtureCommandResult {
	results := make(chan fixtureCommandResult)
	go func() {
		defer close(results)
		reader := bufio.NewReaderSize(input, fixtureCommandMaxBytes+1)
		for {
			line, err := reader.ReadSlice('\n')
			if errors.Is(err, bufio.ErrBufferFull) || (errors.Is(err, io.EOF) && len(line) != 0) || (err != nil && !errors.Is(err, io.EOF)) {
				results <- fixtureCommandResult{err: errFixtureCommandInput}
				return
			}
			if errors.Is(err, io.EOF) {
				return
			}
			if len(line) < 1 || len(line)-1 > fixtureCommandMaxBytes {
				results <- fixtureCommandResult{err: errFixtureCommandInput}
				return
			}
			if command := strings.TrimSpace(string(line[:len(line)-1])); command != "" {
				results <- fixtureCommandResult{command: command}
			}
		}
	}()
	return results
}

func collectFixtureCommands(input io.Reader) []fixtureCommandResult {
	var results []fixtureCommandResult
	for result := range fixtureCommands(input) {
		results = append(results, result)
	}
	return results
}

func TestFixtureCommandsPreserveFragmentedAndCoalescedLines(t *testing.T) {
	input, writer := io.Pipe()
	writeDone := make(chan error, 1)
	go func() {
		for _, fragment := range []string{"grant ", "allowed\n", "totp allowed\ntotp denied\n"} {
			if _, err := io.WriteString(writer, fragment); err != nil {
				writeDone <- err
				return
			}
		}
		writeDone <- writer.Close()
	}()
	results := collectFixtureCommands(input)
	if err := <-writeDone; err != nil {
		t.Fatal(err)
	}
	if len(results) != 3 || results[0].command != "grant allowed" || results[1].command != "totp allowed" || results[2].command != "totp denied" {
		t.Fatalf("framed commands = %#v", results)
	}
	for _, result := range results {
		if result.err != nil {
			t.Fatalf("valid command failed: %v", result.err)
		}
	}
}

func TestFixtureCommandsFailClosedForOverlongAndTruncatedInput(t *testing.T) {
	for name, input := range map[string]io.Reader{
		"overlong":  strings.NewReader(strings.Repeat("x", fixtureCommandMaxBytes+1) + "\n"),
		"truncated": strings.NewReader("partial-command"),
	} {
		t.Run(name, func(t *testing.T) {
			results := collectFixtureCommands(input)
			if len(results) != 1 || !errors.Is(results[0].err, errFixtureCommandInput) || results[0].command != "" {
				t.Fatalf("input result = %#v", results)
			}
			if results[0].err.Error() != "fixture command input denied" || strings.Contains(results[0].err.Error(), "partial-command") {
				t.Fatalf("input error = %q", results[0].err)
			}
		})
	}
}

func TestFixtureCommandsAcceptExactLimitAndCloseAfterCompleteLine(t *testing.T) {
	command := strings.Repeat("a", fixtureCommandMaxBytes)
	results := collectFixtureCommands(strings.NewReader(command + "\n\n"))
	if len(results) != 1 || results[0].err != nil || results[0].command != command {
		t.Fatalf("complete EOF results = %#v", results)
	}
}
