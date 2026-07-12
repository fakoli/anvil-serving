"""External benchmark source adapters."""

from .manual import ManualAdapter
from .llmrequirements import LlmRequirementsAdapter
from .millstone import MillstoneAdapter
from .rtx6kpro import Rtx6kproAdapter

ADAPTERS = {
    "manual": ManualAdapter(),
    "llmrequirements": LlmRequirementsAdapter(),
    "millstone": MillstoneAdapter(),
    "rtx6kpro": Rtx6kproAdapter(),
}

__all__ = [
    "ADAPTERS",
    "LlmRequirementsAdapter",
    "ManualAdapter",
    "MillstoneAdapter",
    "Rtx6kproAdapter",
]
