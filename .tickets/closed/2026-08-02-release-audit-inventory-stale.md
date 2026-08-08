# Release audit inventory was generated before final files were tracked

## Status

Fixed and regression-tested.

## Symptom

Every GitHub Actions test and package-build job for the DeepSeek 0.20.0 pull
request failed while docs and Ruff passed. The repository-scope audit expected
the committed full inventory to match a clean checkout, but CI scanned 576
files and the locally generated inventory described the earlier 566-file set.

## Root cause

The full CLI reference inventory was updated while several new campaign
tickets and recipes were still untracked. The local audit correctly ignored
those files at generation time. Staging them afterward changed the clean
repository scope without regenerating its checked-in inventory.

## Fix

Regenerate the full-scope inventory only after every intended release file is
tracked. Keep the inventory test as the independent clean-checkout guard; do
not weaken it or add exclusions for the new files.

## Verification

- `python scripts/audit_cli_references.py --update --scope full`
- `python scripts/audit_cli_references.py --check --scope full`
- `python -m pytest tests/test_cli_reference_audit.py -q`
- Rebuild and smoke-test the release wheel from the corrected commit.
