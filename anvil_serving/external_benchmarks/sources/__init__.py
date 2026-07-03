"""External benchmark source adapters."""

from .manual import ManualAdapter
from .millstone import MillstoneAdapter
from .rtx6kpro import Rtx6kproAdapter

ADAPTERS = {
    "manual": ManualAdapter(),
    "millstone": MillstoneAdapter(),
    "rtx6kpro": Rtx6kproAdapter(),
}

__all__ = ["ADAPTERS", "ManualAdapter", "MillstoneAdapter", "Rtx6kproAdapter"]
