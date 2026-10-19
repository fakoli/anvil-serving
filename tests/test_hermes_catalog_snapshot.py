"""The explicit native snapshot preserves six-key closure with one process."""
import json
import subprocess
import unittest

from anvil_serving.client_catalog_sync import (
    ClientCatalogError, HERMES_PROFILE_KEYS, _read_hermes_profile,
)


class HermesCatalogSnapshotTests(unittest.TestCase):
    def test_four_profiles_use_four_native_queries_and_preserve_optional_absence(self):
        calls = []
        values = {key: {} for key in HERMES_PROFILE_KEYS if key != "custom_providers"}

        def run(argv, **options):
            calls.append((argv, options))
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "schema": "hermes-catalog-snapshot/v1", "values": values,
                "missing": ["custom_providers"],
            }))

        for profile in ("default", "primary", "secondary", "work"):
            actual = _read_hermes_profile("/opt/hermes", profile, timeout_seconds=7,
                                          run=run, snapshot=True)
            self.assertEqual(actual, {**values, "custom_providers": None})
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(argv[3:] == ["config", "get", "--catalog-snapshot"]
                            and options["timeout"] == 7 for argv, options in calls))

    def test_failed_or_malformed_snapshot_never_falls_back_to_single_key_queries(self):
        valid = {"schema": "hermes-catalog-snapshot/v1",
                 "values": {key: {} for key in HERMES_PROFILE_KEYS}, "missing": []}
        invalid = [
            (1, json.dumps(valid)),
            (0, json.dumps({**valid, "missing": ["model"]})),
            (0, json.dumps({**valid, "values": {"model": {}}})),
            (0, json.dumps({**valid, "values": {**valid["values"], "other": {}}})),
            (0, json.dumps({**valid, "missing": ["custom_providers", "custom_providers"]})),
            (0, '{"schema":"x","schema":"hermes-catalog-snapshot/v1"}'),
            (0, "[]"), (0, "null"), (0, "x" * (2 * 1024 * 1024 + 1)),
        ]
        for rc, output in invalid:
            calls = []
            def run(argv, **options):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, rc, output)
            with self.subTest(rc=rc, bytes=len(output)), self.assertRaises(ClientCatalogError):
                _read_hermes_profile("/opt/hermes", "default", timeout_seconds=7,
                                     run=run, snapshot=True)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
