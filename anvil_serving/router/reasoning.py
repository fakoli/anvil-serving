"""Shared text-reasoning extensions for OpenAI-compatible wire messages."""

from typing import Mapping, Optional


REASONING_TEXT_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")


def extract_reasoning_text(message: object) -> Optional[str]:
    """Select one response alias in priority order without duplicating text.

    Empty or non-string aliases do not mask a later valid alias. Preserve
    whitespace because an individual streaming delta may contain only spaces.
    Request history retains its original wire fields instead of using this
    output normalization.
    """
    if isinstance(message, Mapping):
        for field in REASONING_TEXT_FIELDS:
            value = message.get(field)
            if isinstance(value, str) and value:
                return value
    return None
