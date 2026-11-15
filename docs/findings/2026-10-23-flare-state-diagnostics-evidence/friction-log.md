# Flare state diagnostics — retained friction

## Validation environment

The initial full product test invocation stopped after 51 passing tests. The
failure was `tests/connect/test_application_roles.py::test_file_custody_digest_mode_links_and_checkout` because its temporary path under `/tmp` had a
`.git` ancestor, which the custody guard rejects. The retained receipt is
`validation/product-pytest.log`. This campaign made no product-source change or
product-defect claim from that environment-specific failure.

The first lint invocation also failed because the existing project `.venv` did
not contain Ruff (`validation/product-ruff.log`). The established `uvx ruff
check .` launcher later passed; its receipt and SHA are recorded in
`validation/early-checks.json` and `validation/product-ruff-r2.log`. No package
was installed into the project environment.

A bounded full-test retry with `TMPDIR=OPERATOR_PATH_268766243e9d/flare-validation-tmp` completed after documentation freeze: 857 passed, 5 skipped, 62 subtests passed, and one pre-existing Connect `user_memory` fixture error remained. Its receipts are `validation/product-pytest-r2.log` and `validation/product-pytest-r2-receipt.json`. No product-source change or unrelated Connect repair was made; the broad suite is therefore not all green and this friction entry remains visible.

## Evidence controls

Artifact-set controls were created after the first live action. The initial
protected attempt, failed native state Window 1, repair steps, and recovery
receipts therefore remain included as evidence; later controls are not
represented as pre-run evidence.

## Interpretation controls

The closed paired trace has complete capture coverage, but it lacks a binding
from HTTP requests to scheduler slots or generations and mixes graph shapes
between arms. It is retained as diagnostic evidence pending independent causal
review, never as a timing or production-cause proof.
