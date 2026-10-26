"""Bounded synthetic GPU regression, run by the candidate recipe before weights."""

import importlib.util
import json
from pathlib import Path
import sys

from verify_patch import ORIGINAL_SHA256, PATCHED_SHA256, check_hash


def main():
    if sys.flags.optimize:
        raise RuntimeError("Regression assertions require Python optimization disabled")
    import torch
    from vllm.v1.sample.ops import topk_topp_triton as patched

    assert torch.cuda.is_available(), "CUDA is required; CPU fallback is forbidden"
    check_hash(Path(patched.__file__), PATCHED_SHA256)
    baseline_path = Path(__file__).with_name("baseline.py")
    check_hash(baseline_path, ORIGINAL_SHA256)
    specification = importlib.util.spec_from_file_location("anvil_sampling_baseline", baseline_path)
    baseline = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = baseline
    specification.loader.exec_module(baseline)
    torch.cuda.set_device(0)
    generator = torch.Generator(device="cuda").manual_seed(58231)
    torch.cuda.reset_peak_memory_stats()
    forced_cases = parity_cases = 0
    for vocab_size, forced_token in ((131072, 13), (154856, 154842)):
        for batch_size in (4, 64):
            ordinary = torch.randn(batch_size, vocab_size, generator=generator,
                                   device="cuda", dtype=torch.float32)
            top_p = torch.full((batch_size,), 0.95, device="cuda", dtype=torch.float32)
            before = baseline.apply_top_k_top_p_triton(ordinary.clone(), None, top_p)
            after = patched.apply_top_k_top_p_triton(ordinary.clone(), None, top_p)
            assert torch.equal(before, after), "Ordinary-logit exact mask parity failed"
            assert not after.softmax(-1).isnan().any()
            parity_cases += 1
            del before, after
            for forced_logit in (1e9, 1e9 / 0.6):
                ordinary[:, forced_token] = forced_logit
                before = baseline.apply_top_k_top_p_triton(ordinary.clone(), None, top_p)
                assert torch.isneginf(before).all(), "Original did not reproduce all-masked defect"
                before_probabilities = before.softmax(-1)
                assert before_probabilities.isnan().all()
                assert (before_probabilities.argmax(-1) == 0).all()
                del before, before_probabilities
                after = patched.apply_top_k_top_p_triton(ordinary.clone(), None, top_p)
                assert (torch.isfinite(after).sum(-1) == 1).all()
                assert (after[:, forced_token] == forced_logit).all()
                probabilities = after.softmax(-1)
                assert not probabilities.isnan().any()
                assert (probabilities.argmax(-1) == forced_token).all()
                forced_cases += 1
                del after, probabilities
            del ordinary, top_p
    torch.cuda.synchronize()
    peak_bytes = torch.cuda.max_memory_allocated()
    assert peak_bytes < 1024 ** 3, "Regression tensor allocation exceeded 1 GiB"
    print("ANVIL_SAMPLING_REGRESSION " + json.dumps({
        "passed": 1, "forced_cases": forced_cases, "parity_cases": parity_cases,
        "peak_tensor_bytes": peak_bytes,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
