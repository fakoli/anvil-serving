"""Independent stdlib fp64 top-p oracle for finite synthetic float32 inputs."""

import hashlib
import math
import struct


class OracleBoundaryAmbiguous(ValueError):
    """Compensated scan and independent boundary summation disagree."""


def _prefix_boundary(weights, order, target):
    running = compensation = 0.0
    count = len(order)
    for position, token in enumerate(order):
        adjusted = weights[token] - compensation
        updated = running + adjusted
        compensation = (updated - running) - adjusted
        running = updated
        if running >= target:
            count = position + 1
            break
    retained = math.fsum(weights[i] for i in order[:count])
    removed = math.fsum(weights[i] for i in order[:count - 1])
    if not removed < target <= retained:
        raise OracleBoundaryAmbiguous("oracle_boundary_ambiguous")
    return count


def stable_top_p_reference(logits, top_p):
    """Certified binary64 numerical prefix, not an exact-real nucleus proof.

    Callers preserve actual float32 inputs. Stable sorting gives lower token
    IDs priority at exact ties. Kahan locates a weight prefix; two math.fsum
    boundary checks certify it against p * normalizer without any tolerance.
    """
    if not logits or not 0 < top_p <= 1 or not all(map(math.isfinite, logits)):
        raise ValueError("Reference requires finite logits and 0 < top_p <= 1")
    maximum = max(logits)
    weights = [math.exp(value - maximum) for value in logits]
    total = math.fsum(weights)
    # Python's stable sort preserves initial index order at equal logits.
    order = sorted(range(len(logits)), key=logits.__getitem__, reverse=True)
    count = _prefix_boundary(weights, order, top_p * total)
    return [weight / total for weight in weights], order, count


def _indices(indices):
    ordered = sorted(indices)
    return {"count": len(ordered), "first32": ordered[:32],
            "sha256_u32be": hashlib.sha256(b"".join(struct.pack(">I", i) for i in ordered)).hexdigest()
            if len(ordered) > 32 else None}


def float32_sha256(values):
    return hashlib.sha256(struct.pack(">" + "f" * len(values), *values)).hexdigest()


def compare_masks(logits, top_p, baseline_mask, patched_mask):
    """Return row diagnostics and the predeclared conservative acceptance gate."""
    if len(logits) != len(baseline_mask) or len(logits) != len(patched_mask):
        raise ValueError("Mask length mismatch")
    if any(type(value) is not bool for mask in (baseline_mask, patched_mask) for value in mask):
        raise ValueError("Masks must contain booleans")
    input_sha256 = float32_sha256(logits)
    try:
        probabilities, order, reference_count = stable_top_p_reference(logits, top_p)
    except OracleBoundaryAmbiguous:
        return {"passed": False, "input_sha256_f32be": input_sha256, "oracle_error": "oracle_boundary_ambiguous",
                "checks": {"oracle_boundary_certified": False},
                "baseline": {"exact": False}, "patched": {"exact": False}}
    reference = set(order[:reference_count])
    masks = [set(i for i, keep in enumerate(mask) if keep)
             for mask in (baseline_mask, patched_mask)]
    def mass(indices):
        return math.fsum(probabilities[i] for i in indices)
    def boundary(token):
        return {"token": token, "logit": logits[token], "probability": probabilities[token]}
    def metrics(kept):
        missing, extra = reference - kept, kept - reference
        lowest = next((i for i in reversed(order) if i in kept), None)
        return {"exact": kept == reference, "kept_count": len(kept),
                "missing_count": len(missing), "extra_count": len(extra),
                "missing_mass": mass(missing), "extra_mass": mass(extra),
                "retained_mass": mass(kept),
                "remove_lowest_mass": mass(kept - {lowest}),
                "prefix_holes": sum(i not in kept for i in order[:len(kept)]),
                "boundary": boundary(lowest) if lowest is not None else None,
                "reference_difference": _indices(missing | extra)}
    baseline, patched = (metrics(mask) for mask in masks)
    count_error = abs(patched["kept_count"] - reference_count)
    checks = {
        "baseline_contains_reference": baseline["missing_count"] == 0,
        "patched_contains_reference": patched["missing_count"] == 0,
        "patched_is_canonical_prefix": patched["prefix_holes"] == 0,
        "patched_difference_not_worse": patched["reference_difference"]["count"] <= baseline["reference_difference"]["count"],
        "patched_count_error_bounded": count_error <= 3 or count_error * 200 < reference_count,
    }
    return {"passed": all(checks.values()), "checks": checks, "input_sha256_f32be": input_sha256,
            "reference": {"kept_count": reference_count, "retained_mass": mass(reference),
                          "remove_lowest_mass": mass(set(order[:reference_count - 1])),
                          "boundary": boundary(order[reference_count - 1])},
            "baseline": baseline, "patched": patched,
            "version_difference": _indices(masks[0] ^ masks[1])}


def compare_outputs(logits, top_p, baseline_output, patched_output):
    """Validate exact fp32 preservation before interpreting kernel masks."""
    input_bits = memoryview(struct.pack("=" + "f" * len(logits), *logits)).cast("I")
    masks, invalid_counts = [], []
    for output in (baseline_output, patched_output):
        if len(output) != len(logits):
            raise ValueError("Output length mismatch")
        output_bits = memoryview(struct.pack("=" + "f" * len(output), *output)).cast("I")
        kept = [math.isfinite(value) for value in output]
        invalid_counts.append(sum((output_bits[i] != input_bits[i]) if finite
                                  else output[i] != -math.inf
                                  for i, finite in enumerate(kept)))
        masks.append(kept)
    result = compare_masks(logits, top_p, *masks)
    result["invalid_output_counts"] = invalid_counts
    result["checks"]["valid_mask_outputs"] = not any(invalid_counts)
    result["passed"] = all(result["checks"].values())
    return result
