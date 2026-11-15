"""Bounded synthetic GPU regression, run by the candidate recipe before weights."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from reference import UPSTREAM_POLICY, compare_outputs, float32_sha256, upstream_count_tolerance
from verify_patch import ORIGINAL_SHA256, PATCHED_SHA256, check_hash

NATIVE_COMPARATOR_SHA256 = "1125d27ad9314ddd5ee27bf3f651e18fa0adfb5c3c9364765e9e17683b7e34c6"

SHAPES = ((131072, 13, 4), (131072, 13, 64),
          (154856, 154842, 4), (154856, 154842, 64))


def receipt(kind, fields):
    print("ANVIL_SAMPLING_" + kind + " " + json.dumps(
        {"policy": UPSTREAM_POLICY, **fields}, sort_keys=True, separators=(",", ":"), allow_nan=False), flush=True)


def main():
    if sys.flags.optimize:
        raise RuntimeError("Regression assertions require Python optimization disabled")
    check_hash(Path(__file__).with_name("native_comparator.py"), NATIVE_COMPARATOR_SHA256)
    import native_comparator
    check_hash(Path(native_comparator.__file__), NATIVE_COMPARATOR_SHA256)
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
    torch.cuda.reset_peak_memory_stats()
    generator = torch.Generator(device="cuda").manual_seed(58231)
    receipt("INPUTS", {"baseline_sha256": ORIGINAL_SHA256, "patched_sha256": PATCHED_SHA256,
            "helpers": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                        for name in ("reference.py", "kernel_regression.py", "verify_patch.py", "native_comparator.py")},
            "native_comparator": {"function": "apply_top_k_top_p_pytorch",
                "sha256": NATIVE_COMPARATOR_SHA256,
                "upstream_commit": "0a1276e5e3d9000488e7590cc0957edd709f2152"},
            "ordinary_seeds": [58231, 42], "shapes": SHAPES})
    forced_cases = 0
    # Preserve the original seed, shape and draw order; run all forced cases first.
    for vocab_size, forced_token, batch_size in SHAPES:
        ordinary = torch.randn(batch_size, vocab_size, generator=generator,
                               device="cuda", dtype=torch.float32)
        top_p = torch.full((batch_size,), 0.95, device="cuda", dtype=torch.float32)
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
            receipt("FORCED_CASE", {"case": forced_cases, "vocabulary": vocab_size,
                    "rows": batch_size, "forced_token": forced_token,
                    "forced_logit": forced_logit, "passed": True})
            del after, probabilities
        del ordinary, top_p

    ordinary_cases = failed_rows = diagnostic_rows = failed_native_batches = 0
    for seed in (58231, 42):
        generator.manual_seed(seed)
        for vocab_size, _, batch_size in SHAPES:
            ordinary = torch.randn(batch_size, vocab_size, generator=generator,
                                   device="cuda", dtype=torch.float32)
            top_p = torch.full((batch_size,), 0.95, device="cuda", dtype=torch.float32)
            # Capture before either clone; bitwise comparison detects input mutation.
            logits_cpu = ordinary.cpu()
            original_row_hashes = [float32_sha256(row.tolist()) for row in logits_cpu]
            native = native_comparator.apply_top_k_top_p_pytorch(ordinary.clone(), None, top_p)
            native_counts = native.isfinite().sum(-1).cpu().tolist()
            native_invalid = (~(native.isneginf() | (native.view(torch.int32) ==
                              ordinary.view(torch.int32)))).sum(-1).cpu().tolist()
            del native
            before = baseline.apply_top_k_top_p_triton(ordinary.clone(), None, top_p)
            after = patched.apply_top_k_top_p_triton(ordinary.clone(), None, top_p)
            input_unchanged = torch.equal(ordinary.view(torch.int32).cpu(), logits_cpu.view(torch.int32))
            baseline_counts = before.isfinite().sum(-1).cpu().tolist()
            patched_counts = after.isfinite().sum(-1).cpu().tolist()
            native_tolerance = {"baseline": upstream_count_tolerance(native_counts, baseline_counts),
                                "patched": upstream_count_tolerance(native_counts, patched_counts)}
            failed_native_batches += not all(item["passed"] for item in native_tolerance.values())
            baseline_outputs = before.cpu()
            patched_outputs = after.cpu()
            actual_top_p = float(top_p[0].item())
            del ordinary, top_p, before, after
            case_failed = case_differing = case_version_differing = 0
            baseline_exact = patched_exact = 0
            input_digest = hashlib.sha256()
            for row in range(batch_size):
                result = compare_outputs(logits_cpu[row].tolist(), actual_top_p,
                    baseline_outputs[row].tolist(), patched_outputs[row].tolist(), policy=UPSTREAM_POLICY)
                input_digest.update(bytes.fromhex(result["input_sha256_f32be"]))
                result["checks"]["input_unchanged"] = (input_unchanged and
                    result["input_sha256_f32be"] == original_row_hashes[row])
                result["checks"]["native_output_valid"] = native_invalid[row] == 0
                result["passed"] = all(result["checks"].values())
                case_failed += not result["passed"]
                case_differing += not (result["baseline"]["exact"] and result["patched"]["exact"])
                baseline_exact += result["baseline"]["exact"]
                patched_exact += result["patched"]["exact"]
                version_differs = result.get("version_difference", {}).get("count", 0) > 0
                case_version_differing += version_differs
                if not result["passed"] or version_differs:
                    result["failed_checks"] = [name for name, passed in result.pop("checks").items() if not passed]
                    receipt("ORDINARY_ROW", {"seed": seed, "vocabulary": vocab_size,
                            "rows": batch_size, "row": row, "top_p": actual_top_p, **result})
                    diagnostic_rows += 1
            ordinary_cases += 1
            failed_rows += case_failed
            receipt("ORDINARY_CASE", {"seed": seed, "vocabulary": vocab_size,
                    "rows": batch_size, "failed_rows": case_failed,
                    "differing_reference_rows": case_differing,
                    "exact_reference_rows": batch_size - case_differing,
                    "baseline_exact_reference_rows": baseline_exact,
                    "patched_exact_reference_rows": patched_exact,
                    "version_difference_rows": case_version_differing,
                    "input_sha256_of_row_hashes": input_digest.hexdigest(),
                    "input_unchanged": input_unchanged,
                    "native_counts": native_counts, "baseline_counts": baseline_counts,
                    "patched_counts": patched_counts, "native_count_tolerance": native_tolerance})
            del logits_cpu, baseline_outputs, patched_outputs
    torch.cuda.synchronize()
    peak_bytes = torch.cuda.max_memory_allocated()
    passed = failed_rows == 0 and failed_native_batches == 0 and peak_bytes < 1024 ** 3
    receipt("REGRESSION", {"passed": passed, "forced_cases": forced_cases,
            "ordinary_cases": ordinary_cases, "ordinary_rows": 272,
            "failed_rows": failed_rows, "diagnostic_rows": diagnostic_rows,
            "failed_native_batches": failed_native_batches, "peak_tensor_bytes": peak_bytes})
    assert passed, "Independent top-p reference or tensor-memory gate failed"


if __name__ == "__main__":
    main()
