"""Offline observation/reconciliation checks; no torch, GPU or model is imported."""

from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def test_gpu_snapshots_reconcile_boundary_cropped_prefix_and_slot_reuse():
    trace, states, budget, lines, syncs = fixture()
    events = trace.before_budget(budget, Logits(), [0], Tensor([0, 0]),
                                Tensor([20, trace_module.END]), Tensor([0, 1]))
    events.values = [[1, 1, trace_module.END, 0, -1, 65, 64, 0],
                     [1, 0, -1, 0, 65, 66, 65, -1]]
    budget.cached_last_start.values = [0]
    budget.cached_scan_pos.values = [65]
    trace.after_budget(budget, Logits(), events)
    selected, count, rejected = Tensor([[trace_module.END, 21]]), Tensor([2]), Tensor([0])
    trace.after_verify(Logits(), selected, count)
    boundary = SimpleNamespace(metadata=Tensor([[1, 1, 0, 1, 513, 1, 0]]),
                               stop_tokens=Tensor([[trace_module.END]]))
    trace.before_commit(Tensor([0]), selected, count, rejected, Tensor([0, 2]), boundary, False)
    # Simulate boundary handling truncating accepted count before actual history write.
    count.values, rejected.values = [1], [1]
    states.total_len.gpu.values = [66]
    states.all_token_ids.gpu.values[0].append(trace_module.END)
    states.last_sampled_tokens.values = [[trace_module.END]]
    trace.after_commit(Tensor([0]), selected, count, rejected, Tensor([1, 0, 0]))
    trace.finish(0)
    records = [json.loads(line.split(" ", 1)[1]) for line in lines]
    step = records[1]
    assert step["before_budget"]["cache"] == [-1, -1, 0]
    assert step["after_budget"]["cache"] == [0, -1, 65]
    assert step["boundary_metadata"] == [1, 1, 0, 1, 513, 1, 0]
    assert step["boundary_stop_tokens"] == [trace_module.END]
    assert step["counts_before_commit"] == [2, 0]
    assert step["counts_after_commit"] == [1, 1]
    assert step["committed"][0]["token"] == trace_module.END
    assert step["committed"][0]["marker_matches_store"] is True
    assert step["after_commit"]["markers"][str(trace_module.END)] == [65]
    assert records[-1]["generated"] == 65 and len(syncs) == 5
    trace.add_request(0, SimpleNamespace(thinking_token_budget=64, max_tokens=512))
    assert trace.slots[0]["request"] == 2


def test_unforced_marker_is_not_classified_as_a_budget_store():
    event = [[1, 0, -1, 0, 65, 126, 125, -1]]
    result = trace_module.reconcile_commit(126, 127, [trace_module.END], [trace_module.END], event)
    assert result[0]["marker_matches_store"] is False
    with pytest.raises(ValueError, match="commit_prefix"):
        trace_module.reconcile_commit(126, 127, [20], [trace_module.END], event)
    with pytest.raises(ValueError, match="unobserved_budget"):
        trace_module.reconcile_commit(126, 127, [20], [20], [[-1] * 8])


def test_default_off_nonprobe_and_explicit_overflow(monkeypatch):
    monkeypatch.delenv("ANVIL_MARKER_TRACE", raising=False)
    off = trace_module.MarkerTrace(None)
    off.add_request(0, None)
    assert not off.enabled and off.before_budget(None, None, None, None, None, None) is None
    trace, _, _, lines, _ = fixture()
    trace.add_request(1, SimpleNamespace(thinking_token_budget=49152, max_tokens=512))
    assert 1 not in trace.slots
    with pytest.raises(RuntimeError, match="requires_c1"):
        trace.selected([0, 1])
    assert json.loads(lines[-1].split(" ", 1)[1])["kind"] == "invalid"
    trace, _, _, lines, _ = fixture()
    trace.byte_count = trace_module.MAX_BYTES
    with pytest.raises(RuntimeError, match="payload_overflow"):
        trace.emit("step")
    assert json.loads(lines[-1].split(" ", 1)[1])["reason"] == "payload_overflow"


def test_closed_docker_context_and_observation_only_patch():
    docker = (DIRECTORY / "Dockerfile").read_text()
    files = ["marker-trace.patch", "source-hashes.json", "anvil_marker_trace.py",
             "install.py", "gpu_regression.py", "launch.py", "README.md", "UPSTREAM-LICENSE"]
    for name in files:
        assert hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest() in docker
    patch = (DIRECTORY / "marker-trace.patch").read_text()
    assert "topk_topp_triton.py" not in patch
    assert "+    tl.store(logits_ptr" not in patch
    assert "trace_events_ptr + token_idx * 8 + 1, 1" in patch
    bindings = json.loads((DIRECTORY / "source-hashes.json").read_text())
    assert len(bindings) == 5
    for row in bindings.values():
        assert len(row["original"]) == len(row["patched"]) == 64
    assert "timeout=120" in (DIRECTORY / "launch.py").read_text()
