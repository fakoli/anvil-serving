"""External benchmark source adapters."""

from .manual import ManualAdapter
from .millstone import MillstoneAdapter

ADAPTERS = {
    "manual": ManualAdapter(),
    "millstone": MillstoneAdapter(),
}

__all__ = ["ADAPTERS", "ManualAdapter", "MillstoneAdapter"]
