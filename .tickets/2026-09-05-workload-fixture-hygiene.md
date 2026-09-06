# Synthetic workload credential scanner findings

Status: source-only fixture repair verified; final integrated acceptance and
merge are recorded in the merge checkpoint ticket.

The pinned CI Gitleaks image found two generic-api-key matches in the clean
tracked snapshot at `c8e551d4`: the deterministic hexadecimal TOKEN constants
in `test_controller_workload_source.py` and `test_fleet_workload_sources.py`.
These were synthetic test values, not operator credentials or an exposure.

Both fixtures now use an obviously artificial repeated test-only value. Their
minimum credential length, exact forwarding, environment isolation and invalid
input assertions remain intact. Production authentication and scanner rules,
allowlists and negative controls are unchanged. No credential rotation occurred.

A broader local-directory scan also encountered ignored pytest node IDs and
rendered documentation copies. Those are not tracked publication input; the
publication gate uses a clean Git snapshot and CI's clean checkout separately.
Do not turn ignored artifacts into broad path allowlists or a history-clean
claim. The final merge checkpoint records the actual scan results.

At `5f96e7a7`, 79 focused tests and Ruff passed with claim-bound evidence
`EVA54FC316`. A clean Git archive passed the pinned Gitleaks scan with no
findings; the semantic scanner and its intentional-positive self-test also
passed. These are current-snapshot results, not a full-history audit.
