#!/bin/sh
# pre-submit.sh — run this before `anvil submit` or opening a PR.
#
# It enforces the whole-repo invariants that a task-scoped verification cannot
# see, in order, and exits non-zero on ANY failure:
#
#   (a) regenerate the CLI reference audit (docs/CLI-REFERENCE-AUDIT.json,
#       docs/CLI.md generated blocks, and the audit fixture) and FAIL if the
#       regeneration changes any of those files — that means they were stale
#       and the regenerated output must be reviewed and committed;
#   (b) run the full test suite (python -m pytest tests/ -x -q);
#   (c) mirror CI's ruff lint gate when ruff is available.
#
# Root cause it fixes: whole-repo invariants live in a single global test
# (tests/test_cli_reference_audit.py::test_repository_scope_inventories_match).
# A contributor/agent who adds or removes a file, forgets to regenerate the
# audit, and only runs their narrow task tests will pass locally yet red the
# full suite at the review gate. Running this once before submit closes that gap.
#
# POSIX sh; portable across Git Bash on Windows and Linux CI. No non-stdlib deps.

set -u

# Resolve the repo root from this script's location so cwd does not matter.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd) || exit 1
cd "$REPO_ROOT" || exit 1

# Pick a Python interpreter (Git Bash ships `python`; some Linux only `python3`).
if command -v python >/dev/null 2>&1; then
    PY=python
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    printf 'FAIL  no python interpreter found on PATH\n' >&2
    exit 1
fi

FAILED=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1" >&2; FAILED=1; }

printf '== pre-submit: whole-repo invariants ==\n'

# (a) Regenerate the CLI reference audit, then confirm nothing was left dirty.
GENERATED='docs/CLI-REFERENCE-AUDIT.json docs/CLI.md tests/fixtures/cli_reference_audit/expected.json'
SNAPSHOT=$(mktemp -d) || exit 1
trap 'rm -rf "$SNAPSHOT"' EXIT HUP INT TERM

for f in $GENERATED; do
    mkdir -p "$SNAPSHOT/$(dirname "$f")" || exit 1
    if [ -f "$f" ]; then
        cp "$f" "$SNAPSHOT/$f" || exit 1
    else
        : > "$SNAPSHOT/$f.MISSING"
    fi
done

if $PY scripts/audit_cli_references.py --update; then
    stale=''
    for f in $GENERATED; do
        if [ -f "$SNAPSHOT/$f.MISSING" ]; then
            if [ -f "$f" ]; then
                stale="$stale $f"
            fi
        elif ! git diff --quiet --no-index -- "$SNAPSHOT/$f" "$f"; then
            stale="$stale $f"
        fi
    done
    if [ -n "$stale" ]; then
        fail "regenerated stale audit — commit it:$stale"
    else
        pass 'CLI reference audit is fresh'
    fi
else
    # Non-zero here means active legacy-reference violations or a broken audit;
    # both must be fixed before submit.
    fail 'CLI reference audit refused to regenerate (see output above)'
fi

# (b) Full test suite — the gate the review runs.
if $PY -m pytest tests/ -x -q; then
    pass 'full test suite green'
else
    fail 'full test suite red (python -m pytest tests/ -x -q)'
fi

# (c) Ruff lint — a whole-repo gate CI enforces in its own job. Optional here
#     because ruff is not a stdlib/runtime dep; when present we fail on it so the
#     contributor catches it locally instead of at CI.
if command -v ruff >/dev/null 2>&1; then
    if ruff check .; then
        pass 'ruff clean'
    else
        fail 'ruff reported lint violations'
    fi
else
    printf 'SKIP  ruff not installed — CI lint job still enforces this\n'
fi

printf '== pre-submit: '
if [ "$FAILED" -eq 0 ]; then
    printf 'ALL GREEN — safe to submit ==\n'
    exit 0
fi
printf 'RED — do not submit ==\n' >&2
exit 1
