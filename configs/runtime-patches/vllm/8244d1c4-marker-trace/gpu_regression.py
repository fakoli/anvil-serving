"""Small actual-CUDA test of trace stores/cache/commit; run before model weights."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from vllm.v1.core.boundary_checkpoint import NUM_BOUNDARY_CHECKPOINT_SLOTS
from vllm.v1.worker.gpu.input_batch import post_update
from vllm.v1.worker.gpu.sample.anvil_marker_trace import END, START, MarkerTrace
from vllm.v1.worker.gpu.sample.thinking_budget import apply_thinking_budget


def main():
    assert torch.cuda.is_available()
    device = torch.device("cuda:0")
    torch.cuda.reset_peak_memory_stats(device)
    baseline_path = Path(__file__).with_name("baseline-thinking_budget.py")
    spec = importlib.util.spec_from_file_location("marker_original_budget", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    def wrapper(tensor):
        return SimpleNamespace(gpu=tensor)
    states = SimpleNamespace(device=device,
        prompt_len=SimpleNamespace(np=np.array([1])),
        total_len=wrapper(torch.tensor([65], dtype=torch.int32, device=device)),
        num_computed_tokens=wrapper(torch.tensor([65], dtype=torch.int32, device=device)),
        all_token_ids=wrapper(torch.full((1, 640), 20, dtype=torch.int32, device=device)),
        last_sampled_tokens=torch.zeros((1, 1), dtype=torch.int64, device=device))
    states.all_token_ids.gpu[0, 0] = START
    budget = SimpleNamespace(
        thinking_token_budget=wrapper(torch.tensor([64], dtype=torch.int32, device=device)),
        cached_last_start=torch.tensor([-1], dtype=torch.int32, device=device),
        cached_last_end=torch.tensor([-1], dtype=torch.int32, device=device),
        cached_scan_pos=torch.tensor([0], dtype=torch.int32, device=device))
    mapping = torch.tensor([0], dtype=torch.int32, device=device)
    expanded = torch.zeros(4, dtype=torch.int32, device=device)
    local = torch.arange(4, dtype=torch.int32, device=device)
    starts = torch.tensor([START], dtype=torch.int32, device=device)
    ends = torch.tensor([END], dtype=torch.int32, device=device)
    query_start = torch.tensor([0, 4], dtype=torch.int32, device=device)
    inputs = torch.tensor([20, END, 21, 22], dtype=torch.int32, device=device)
    records = []
    trace = MarkerTrace(states, enabled=True, sink=records.append, rank=-1)
    trace.add_request(0, SimpleNamespace(thinking_token_budget=64, max_tokens=512))
    for iteration in range(2):
        logits = torch.zeros((4, 154880), dtype=torch.float32, device=device)
        original_logits = logits.clone()
        original_cache = [value.clone() for value in
                          (budget.cached_last_start, budget.cached_last_end, budget.cached_scan_pos)]
        common = [mapping, expanded, budget.thinking_token_budget.gpu,
                  states.all_token_ids.gpu, states.total_len.gpu, inputs, local]
        baseline.apply_thinking_budget(original_logits, *common, *original_cache,
                                       starts, ends, ends)
        events = trace.before_budget(budget, logits, np.array([0]), expanded, inputs, local)
        apply_thinking_budget(logits, *common, budget.cached_last_start,
                              budget.cached_last_end, budget.cached_scan_pos,
                              starts, ends, ends, trace_events=events)
        trace.after_budget(budget, logits, events)
        torch.cuda.synchronize(device)
        assert torch.equal(logits, original_logits)
        assert all(torch.equal(a, b) for a, b in zip(original_cache,
                   (budget.cached_last_start, budget.cached_last_end, budget.cached_scan_pos), strict=True))
        forced = events[:, 1].cpu().tolist()
        assert forced == ([1, 0, 0, 0] if iteration == 0 else [0, 0, 0, 0])
        selected = torch.tensor([[END, 21, 22, 23]], dtype=torch.int64, device=device)
        count = torch.tensor([1 if iteration == 0 else 2], dtype=torch.int32, device=device)
        rejected = torch.tensor([3 if iteration == 0 else 2], dtype=torch.int32, device=device)
        boundary = None
        capture = None
        if iteration == 1:
            boundary = SimpleNamespace(
                metadata=torch.tensor([[1, 1, 0, 1, 513, 1, 0]], dtype=torch.int32, device=device),
                stop_tokens=torch.full((1, 128), END, dtype=torch.int32, device=device),
                seen=torch.zeros((1, NUM_BOUNDARY_CHECKPOINT_SLOTS), dtype=torch.int32, device=device))
            capture = torch.zeros((3, 1, NUM_BOUNDARY_CHECKPOINT_SLOTS), dtype=torch.int32, device=device)
        trace.after_verify(logits, selected, count)
        trace.before_commit(mapping, selected, count, rejected, query_start, boundary, False)
        post_update(mapping, states.num_computed_tokens.gpu, states.last_sampled_tokens,
                    None, selected, count, rejected, query_start,
                    states.all_token_ids.gpu, states.total_len.gpu, boundary, capture)
        trace.after_commit(mapping, selected, count, rejected, capture)
        inputs[0] = END
    trace.finish(0)
    parsed = [json.loads(line.split(" ", 1)[1]) for line in records]
    steps = [row for row in parsed if row["kind"] == "step"]
    assert len(steps) == 2
    assert steps[0]["committed"][0]["marker_matches_store"] is True
    assert steps[1]["committed"][0]["marker_matches_store"] is False
    assert steps[1]["after_budget"]["cache"][1] == 65
    assert steps[1]["counts_before_commit"] == [2, 2]
    assert steps[1]["counts_after_commit"] == [1, 3]
    assert steps[1]["boundary_stop_tokens"] == [END]
    peak = torch.cuda.max_memory_allocated(device)
    assert peak < 64 * 1024 * 1024
    print("ANVIL_MARKER_TRACE " + json.dumps({"schema": "marker-trace/v1",
        "kind": "gpu_selftest", "passed": True, "steps": 2,
        "original_logits_and_cache_equal": True, "peak_tensor_bytes": peak,
        "limit": "Synthetic selected tokens; does not test stochastic rejection sampling."}), flush=True)


if __name__ == "__main__":
    from vllm.utils.gpu_sync_debug import gpu_sync_allowed
    with gpu_sync_allowed():
        main()
