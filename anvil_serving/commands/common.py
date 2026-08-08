"""Shared command options used across command families."""

from .spec import _option


ACTION_OPTIONS = (_option("--dry-run", summary="Preview without mutating state."),)
CONFIRM_OPTIONS = ACTION_OPTIONS + (_option("--confirm", summary="Confirm the guarded mutation."),)
