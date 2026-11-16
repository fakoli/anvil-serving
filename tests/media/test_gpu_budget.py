"""Test the budget hooks independently of CUDA or the media model."""
import importlib
from types import SimpleNamespace

import pytest

from anvil_serving.media import gpu_budget


def fresh():
    return importlib.reload(gpu_budget)


def fake_cuda():
    state = SimpleNamespace(fraction=1.0)
    cuda = SimpleNamespace(is_available=lambda: True, get_allocator_backend=lambda: "native",
        current_device=lambda: 0, get_device_properties=lambda _: SimpleNamespace(total_memory=32*1024**3),
        set_per_process_memory_fraction=lambda value, _: setattr(state, "fraction", value),
        get_per_process_memory_fraction=lambda _: state.fraction,
        memory_allocated=lambda _: 10*1024**3, memory_reserved=lambda _: 11*1024**3)
    return SimpleNamespace(cuda=cuda)


def test_budget_clamps_estimates_and_allocator_without_changing_host_memory():
    hooks = fresh()
    torch = fake_cuda()
    args = SimpleNamespace(disable_dynamic_vram=True, disable_cuda_malloc=True)
    receipt = hooks.configure(torch, args, environ={hooks.ENV: "13312"})
    device = SimpleNamespace(type="cuda", index=0)
    assert receipt["limitBytes"] == 13*1024**3
    assert hooks.total_memory(device, 32*1024**3) == 13*1024**3
    assert hooks.free_memory(device, 20*1024**3, 1024**3) == (3*1024**3, 1024**3)
    assert hooks.free_memory(device, 2*1024**3, 1024**3) == (2*1024**3, 1024**3)
    assert hooks.total_memory(SimpleNamespace(type="cpu"), 64*1024**3) == 64*1024**3
    with pytest.raises(ValueError, match="device"):
        hooks.total_memory(SimpleNamespace(type="cuda", index=1), 32*1024**3)
    torch.cuda.get_per_process_memory_fraction = lambda _: 1.0
    with pytest.raises(ValueError, match="changed"):
        hooks.status()


def test_budget_refuses_unbounded_backends_and_invalid_limits():
    for flags, raw in [(False,"13312"), (True,"0"), (True,"nan"), (True,"32768"), (True,"１３３１２")]:
        hooks=fresh()
        with pytest.raises(ValueError):
            hooks.configure(fake_cuda(), SimpleNamespace(disable_dynamic_vram=flags, disable_cuda_malloc=True),
                            environ={hooks.ENV:raw})
    hooks=fresh(); torch=fake_cuda(); torch.cuda.get_allocator_backend=lambda:"cudaMallocAsync"
    with pytest.raises(ValueError, match="native CUDA"):
        hooks.configure(torch, SimpleNamespace(disable_dynamic_vram=True,disable_cuda_malloc=True),environ={hooks.ENV:"13312"})
    hooks=fresh()
    assert hooks.configure(fake_cuda(),SimpleNamespace(),environ={}) is None
    assert hooks.status() is None
    assert hooks.free_memory(SimpleNamespace(type="cuda",index=0),100,20)==(100,20)
