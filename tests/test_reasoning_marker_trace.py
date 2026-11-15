"""Offline observation/reconciliation checks; no torch, GPU or model is imported."""

from contextlib import nullcontext
import importlib.util
from pathlib import Path
from types import SimpleNamespace


DIRECTORY = Path(__file__).resolve().parents[1] / "configs/runtime-patches/vllm/8244d1c4-marker-trace"
spec = importlib.util.spec_from_file_location("marker_trace_fixture", DIRECTORY / "anvil_marker_trace.py")
trace_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace_module)


class Tensor:
    def __init__(self, values):
        self.values = values

    def __getitem__(self, index):
        if isinstance(index, tuple):
            row, column = index
            return Tensor(self.values[row][column])
        return Tensor(self.values[index])

    def __eq__(self, token):
        return Tensor([value == token for value in self.values])

    def nonzero(self):
        return Tensor([[i] for i, value in enumerate(self.values) if value])

    def flatten(self):
        return Tensor([value for row in self.values for value in row])

    def cpu(self):
        return self

    def tolist(self):
        return self.values

    def item(self):
        return self.values

    def numel(self):
        return len(self.values)

    @property
    def shape(self):
        return (len(self.values), len(self.values[0]))


class Logits:
    shape = (2, 154880)
    device = "synthetic"

    def __getitem__(self, index):
        assert index == (slice(None), [trace_module.START, trace_module.END])
        return self

    def detach(self):
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return [[0.0, 1e9], [0.0, 0.0]]


def fixture():
    states = SimpleNamespace(device="synthetic", prompt_len=SimpleNamespace(np=[1]),
        total_len=SimpleNamespace(gpu=Tensor([65])),
        num_computed_tokens=SimpleNamespace(gpu=Tensor([65])),
        all_token_ids=SimpleNamespace(gpu=Tensor([[trace_module.START] + [20] * 64])),
        last_sampled_tokens=Tensor([[20]]))
    budget = SimpleNamespace(thinking_token_budget=SimpleNamespace(gpu=Tensor([64])),
        cached_last_start=Tensor([-1]), cached_last_end=Tensor([-1]), cached_scan_pos=Tensor([0]))
    syncs = []
    torch = SimpleNamespace(int32="int32", distributed=SimpleNamespace(is_initialized=lambda: False),
        cuda=SimpleNamespace(is_current_stream_capturing=lambda: False,
                             synchronize=lambda device: syncs.append(device)),
        full=lambda shape, fill, **kwargs: Tensor([[fill] * shape[1] for _ in range(shape[0])]))
    lines = []
    trace = trace_module.MarkerTrace(states, enabled=True, torch_module=torch,
                                    sync_context=nullcontext, sink=lines.append, rank=0)
    trace.add_request(0, SimpleNamespace(thinking_token_budget=64, max_tokens=512))
    return trace, states, budget, lines, syncs
