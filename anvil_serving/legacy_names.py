"""Owner-derived identifiers replaced by generic defaults (docs/MIGRATION-GENERIC-NAMES.md).

Old names keep working for one release; each use emits one LegacyNameWarning.
"""
import warnings

MIGRATION_DOC = "docs/MIGRATION-GENERIC-NAMES.md"

# old name -> generic replacement. The single place the old names live in product code.
LEGACY_NAMES = {
    "fakoli-models": "anvil-models",        # docker volume holding model weights
    "fakoli-experiment": "anvil-experiment",  # compose project (compose files only)
    "fakoli-flexibility": "anvil-flexibility",  # compose project (compose files only)
    "mini": "companion",                   # voice forwarding bridge owner
}


class LegacyNameWarning(FutureWarning):
    """A deprecated owner-derived name is in use."""


def warn_legacy(old, kind, stacklevel=3):
    new = LEGACY_NAMES[old]
    warnings.warn(
        f"{kind} {old!r} is deprecated and will stop working in the next release; "
        f"use {new!r} (see {MIGRATION_DOC})",
        LegacyNameWarning, stacklevel=stacklevel)
