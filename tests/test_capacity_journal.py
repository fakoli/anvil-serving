"""Hermetic capacity journal checks; no endpoint or service is contacted."""
import hashlib
import json
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


if __name__ == "__main__":
    unittest.main()
