"""Manual source adapter for already-normalized JSON/CSV operator fixtures."""
from __future__ import annotations

from .millstone import MillstoneAdapter


class ManualAdapter(MillstoneAdapter):
    name = "manual"
    parser_name = "manual"

