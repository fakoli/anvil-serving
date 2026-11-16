"""Allocator and Comfy memory-estimate bounds for isolated CUDA media workers.

The managed image calls these hooks; the base serving package stays stdlib-only.
Each worker remains single-prompt. The operator must also reserve device/context
headroom and enforce container/host memory limits outside this allocator bound.
"""
from __future__ import annotations

import os
import math

FEATURE = "anvil.media.cuda-budget.v1"
ENV = "ANVIL_MEDIA_CUDA_BUDGET_MIB"
_limit = 0
_device = None
_torch = None


def configure(torch, args, *, environ=None):
    global _limit, _device, _torch
    raw = (os.environ if environ is None else environ).get(ENV)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or not 256 <= int(raw) <= 1048576:
        raise ValueError("invalid media CUDA budget")
    if not args.disable_dynamic_vram or not args.disable_cuda_malloc:
        raise ValueError("bounded worker requires dynamic VRAM and cudaMallocAsync disabled")
    cuda = torch.cuda
    if not cuda.is_available() or cuda.get_allocator_backend() != "native":
        raise ValueError("bounded worker requires the native CUDA caching allocator")
    device = cuda.current_device()
    physical = cuda.get_device_properties(device).total_memory
    limit = int(raw) * 1024 * 1024
    if type(physical) is not int or physical <= limit:
        raise ValueError("media CUDA budget must leave physical device headroom")
    fraction = limit / physical
    cuda.set_per_process_memory_fraction(fraction, device)
    observed = cuda.get_per_process_memory_fraction(device)
    if not isinstance(observed, (int, float)) or not math.isfinite(observed) or abs(observed - fraction) > 1e-9:
        raise ValueError("media CUDA allocator did not enforce the configured budget")
    _limit, _device, _torch = limit, device, torch
    return status()


def _matches(device):
    if not _limit or getattr(device, "type", None) != "cuda":
        return False
    index = getattr(device, "index", None)
    if index not in (None, _device):
        raise ValueError("media CUDA budget does not cover this device")
    return True


def total_memory(device, physical_total):
    return min(physical_total, _limit) if _matches(device) else physical_total


def free_memory(device, physical_free, reusable):
    if not _matches(device):
        return physical_free, reusable
    active = _torch.cuda.memory_allocated(_device)
    if type(active) is not int or active < 0:
        raise ValueError("media CUDA allocation accounting is unavailable")
    remaining = max(0, _limit - active)
    return min(physical_free, remaining), min(reusable, remaining)


def status():
    if not _limit:
        return None
    cuda = _torch.cuda
    physical = cuda.get_device_properties(_device).total_memory
    fraction = cuda.get_per_process_memory_fraction(_device)
    if (cuda.get_allocator_backend() != "native" or not isinstance(fraction, (int, float))
            or not math.isfinite(fraction) or abs(fraction - _limit / physical) > 1e-9):
        raise ValueError("media CUDA allocator budget changed after startup")
    allocated, reserved = cuda.memory_allocated(_device), cuda.memory_reserved(_device)
    if any(type(v) is not int or not 0 <= v <= _limit for v in (allocated, reserved)):
        raise ValueError("media CUDA allocation exceeds the budget")
    return {"schema": FEATURE, "limitBytes": _limit, "device": _device,
            "allocator": cuda.get_allocator_backend(), "dynamicVram": False,
            "allocatedBytes": allocated, "reservedBytes": reserved}
