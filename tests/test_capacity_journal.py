"""Hermetic capacity journal checks; no endpoint or service is contacted."""
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anvil_serving.benchmarking import cli


def arguments(output):
    return [
        "capacity", "--base-url", "http://127.0.0.1:30002/v1",
        "--model", "candidate", "--requests", "2", "--concurrency", "1",
        "--ctx-tokens", "128", "--max-model-len", "8192",
        "--reasoning-effort", "max", "--output", str(output),
    ]


def response():
    return {
        "ttft": 0.1, "time_to_first_output": 0.05, "e2e": 0.2,
        "out_toks": 4, "output_token_source": "usage",
        "content_chunks": 1, "reasoning_chunks": 2,
        "visible_content": "private-response-body",
        "visible_content_captured_chars": 21,
        "visible_content_capture_limit": 8192,
        "finish_reasons": ["stop"], "stream_terminal_observed": True,
        "usage": {"prompt_tokens": 100, "completion_tokens": 4},
    }


def complete_records(raw):
    # A process may die mid-append. Retain only newline-terminated records.
    return [json.loads(line) for line in raw.splitlines(keepends=True)
            if line.endswith(b"\n")]


class CapacityJournalTests(unittest.TestCase):
    def test_process_exit_during_later_request_preserves_first_terminal_result(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capacity.json"
            script = r"""
import importlib.util, json, os, pathlib, sys, time
spec = importlib.util.spec_from_file_location("journal_test", "tests/test_capacity_journal.py")
test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(test)
output = pathlib.Path(sys.argv[1])
journal = pathlib.Path(str(output) + ".journal.jsonl")
calls = 0
def stream(*args, **kwargs):
    global calls
    calls += 1
    if calls == 1:
        return test.response()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if journal.exists() and len(test.complete_records(journal.read_bytes())) == 2:
            os._exit(73)
        time.sleep(0.01)
    os._exit(74)
test.cli.main(test.arguments(output), stream_request=stream)
"""
            process = subprocess.run(
                [sys.executable, "-c", script, str(output)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(process.returncode, 73, process.stderr)
            self.assertFalse(output.exists())
            journal_path = Path(str(output) + ".journal.jsonl")
            raw = journal_path.read_bytes()
            records = complete_records(raw)
            header = records[0]
            self.assertEqual(header["status"], "running")
            self.assertFalse(header["performance_eligible"])
            self.assertEqual(header["requests"], 2)
            self.assertEqual(header["concurrency"], 1)
            self.assertEqual(header["reasoning_effort_requested"], "max")
            self.assertEqual(header["reasoning_effort"], "max")
            self.assertEqual([record["record_type"] for record in records], ["header", "outcome"])
            row = records[1]["outcome"]["response_timing"]
            self.assertTrue(row["stream_terminal_observed"])
            self.assertEqual(row["ttft_ms"], 100)
            self.assertEqual(row["effective_prefill_tok_s"], 2000)
            self.assertAlmostEqual(row["decode_tok_s"], 20)
            self.assertEqual(row["visible_content_captured_chars"], 21)
            self.assertNotIn(b"private-response-body", raw)
            # A partial completion footer cannot turn the interrupted run into success.
            journal_path.write_bytes(raw + b'{"record_type":"completion"')
            self.assertEqual(complete_records(journal_path.read_bytes()), records)

    def test_terminal_success_and_failure_are_distinct_and_hash_bound(self):
        for failure_kind in (None, "request", "validation"):
            with self.subTest(failure=failure_kind), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "capacity.json"
                calls = 0
                def stream(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    result = response()
                    if calls == 2 and failure_kind == "request":
                        raise ValueError("failed credential opaque-test-credential")
                    if calls == 2 and failure_kind == "validation":
                        result.update(ttft=None, content_chunks=0,
                                      visible_content_captured_chars=0)
                    return result
                with patch.object(cli, "resolve_api_key", return_value="opaque-test-credential"):
                    code = cli.main(arguments(output), stream_request=stream)
                self.assertEqual(code, int(failure_kind is not None))
                raw = Path(str(output) + ".journal.jsonl").read_bytes()
                records = complete_records(raw)
                self.assertEqual([record["record_type"] for record in records],
                                 ["header", "outcome", "outcome", "completion"])
                journal = records[-1]
                outcomes = [record["outcome"] for record in records[1:-1]]
                summary = json.loads(output.read_text())
                self.assertEqual(journal["status"], "complete")
                self.assertEqual(journal["performance_eligible"], failure_kind is None)
                self.assertEqual(journal["failed"], int(failure_kind is not None))
                self.assertEqual(len(outcomes), 2)
                self.assertEqual(summary["schema"], "anvil-serving.benchmark/v1")
                self.assertEqual(summary["request_journal"]["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(summary["request_journal"]["bytes"], len(raw))
                self.assertEqual(summary["request_journal"]["path"], "capacity.json.journal.jsonl")
                self.assertNotIn(b"private-response-body", raw)
                self.assertNotIn(b"opaque-test-credential", raw)
                if failure_kind:
                    self.assertIsNone(summary["metrics"]["throughput_tok_s"])
                if failure_kind == "validation":
                    failure = next(row for row in outcomes
                                   if row["request_index"] == 1)
                    self.assertEqual(failure["failure_class"], "stream_validation_failed")
                    self.assertTrue(failure["response_received"])
                    self.assertEqual(failure["response_metadata"]["reasoning_chunks"], 2)
                    self.assertEqual(failure["response_metadata"]["content_chunks"], 0)
                    self.assertIsNone(failure["response_timing"])

    def test_derived_journal_collision_rejected_before_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            output = root / "capacity.json"
            alias = root / "nested" / ".." / "capacity.json.journal.jsonl"
            with patch.object(cli, "stream_chat") as stream:
                with contextlib.redirect_stderr(io.StringIO()) as error:
                    with self.assertRaises(SystemExit) as raised:
                        cli.main(arguments(output) + ["--recipe-out", str(alias)],
                                 stream_request=stream)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("must use distinct paths", error.getvalue())
                stream.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(alias.exists())

    def test_existing_or_raced_partial_journal_is_never_truncated(self):
        for raced in (False, True):
            with self.subTest(raced=raced), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "capacity.json"
                journal = Path(str(output) + ".journal.jsonl")
                original = b'{"record_type":"header"}\n{"record_type":"outcome"'
                if not raced:
                    journal.write_bytes(original)
                def prefix(*args, **kwargs):
                    journal.write_bytes(original)
                    return ""
                with patch.object(cli, "make_shared_prefix", side_effect=prefix):
                    with patch.object(cli, "stream_chat") as stream:
                        with contextlib.redirect_stderr(io.StringIO()) as error:
                            with self.assertRaises(SystemExit) as raised:
                                cli.main(arguments(output), stream_request=stream)
                        self.assertEqual(raised.exception.code, 2)
                        self.assertIn("journal already exists", error.getvalue())
                        stream.assert_not_called()
                self.assertEqual(journal.read_bytes(), original)
                self.assertFalse(output.exists())

    def test_output_still_supports_ten_thousand_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capacity.json"
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(cli.main(arguments(output) + [
                    "--requests", "10000", "--dry-run",
                ]), 0)
            self.assertEqual(json.loads(printed.getvalue())["capacity"]["requests"], 10000)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
