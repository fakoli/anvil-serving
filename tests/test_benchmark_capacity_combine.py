import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "anvil-serving-benchmark-docs"
    / "scripts"
    / "combine_capacity_artifacts.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("capacity_combine", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(
    path: Path,
    *,
    start="2026-09-05T00:00:00Z",
    ctx=4096,
    **overrides,
):
    payload = {
        "schema": "anvil-serving.benchmark/v1",
        "measurement_protocol": "capacity-v3",
        "run_id": "run-" + path.stem,
        "started_at": start,
        "finished_at": "2026-09-05T00:00:10Z",
        "wall_clock_ms": 10000.0,
        "base_url": "http://127.0.0.1:30000/v1",
        "model": path.stem,
        "engine": "sglang",
        "gpu": path.stem,
        "requests": 1,
        "completed": 1,
        "failed": 0,
        "concurrency": 1,
        "context_tokens": ctx,
        "max_context_tokens": 262144,
        "max_tokens": 4,
        "response_words": 2,
        "prompt_cache_mode": "unique",
        "shared_prefix_tokens": 0,
        "request_canaries": True,
        "controlled_output_policy": "observe",
        "context_seed": 0,
        "serve_flags": {
            "shared_prefix_burst": False,
            "thinking_mode": "disabled",
            "reasoning_effort": None,
        },
        "request_timings": [{
            "request_index": 0,
            "time_to_first_output_ms": 100.0,
            "ttft_ms": 100.0,
            "e2e_ms": 200.0,
            "generation_ms": 100.0,
            "effective_prefill_tok_s": 1000.0,
            "decode_tok_s": 30.0,
            "mean_inter_token_latency_ms": 25.0,
            "prompt_tokens": 100,
            "output_tokens": 4,
            "output_token_source": "usage",
            "request_canary": {
                "passed": True, "marker_at_start": True,
                "foreign_marker_count": 0, "capture_complete": True,
            },
            "controlled_output": {
                "policy": "observe", "exact_adherence": True,
                "requested_words": 2, "observed_code_words": 2,
                "observed_extra_words": 0, "capture_complete": True,
                "passed": True,
            },
        }],
        "metrics": {"throughput_tok_s": 0.4, "output_tokens": 4},
    }
    payload.update(overrides)
    if "request_timings" not in overrides:
        row = payload["request_timings"][0]
        if not payload["request_canaries"]:
            row["request_canary"] = None
        if not payload["response_words"] or payload["controlled_output_policy"] is None:
            row["controlled_output"] = None
        else:
            row["controlled_output"].update({
                "policy": payload["controlled_output_policy"],
                "requested_words": payload["response_words"],
                "observed_code_words": payload["response_words"],
            })
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sampling(*, temperature=None, top_p=None):
    return {
        "temperature": {
            "requested": temperature,
            "effective_request": 0.0 if temperature is None else temperature,
            "sent": True,
        },
        "top_p": {
            "requested": top_p,
            "effective_request": top_p,
            "sent": top_p is not None,
        },
    }


@pytest.mark.parametrize(
    "right_sampling",
    (_sampling(temperature=1.0), _sampling(temperature=0.0, top_p=0.95)),
)
def test_combine_rejects_different_declared_sampling(tmp_path, right_sampling):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left, sampling=_sampling(temperature=0.0))
    _artifact(right, sampling=right_sampling)

    with pytest.raises(ValueError, match="sampling differs"):
        _module().combine([left, right])


def test_combine_requires_matching_workload_identity_and_keeps_legacy_inputs(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    identity = {
        "prompt_contract_revision": "capacity-prompt-v1",
        "workload_kind": "synthetic-repeated-code",
    }
    _artifact(left, workload_identity=identity)
    _artifact(right, workload_identity=identity)

    combined = _module().combine([left, right])
    assert combined["workload_identity"] == identity

    _artifact(right)
    with pytest.raises(ValueError, match="workload_identity differs"):
        _module().combine([left, right])

    _artifact(right, workload_identity={
        "prompt_contract_revision": "capacity-prompt-v1",
        "workload_kind": "synthetic-summary",
    })
    with pytest.raises(ValueError, match="workload_identity conflicts"):
        _module().combine([left, right])


def test_combine_requires_matching_configuration_fingerprints_when_present(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    first = "sha256:" + "a" * 64
    second = "sha256:" + "b" * 64
    matched_identity = {
        "model": "replicated-model",
        "source_recipe": "configs/replica.toml#model",
    }
    _artifact(left, configuration_fingerprint=first, **matched_identity)
    _artifact(right, configuration_fingerprint=second, **matched_identity)
    with pytest.raises(ValueError, match="configuration_fingerprint"):
        _module().combine([left, right])

    _artifact(right, configuration_fingerprint=first, **matched_identity)
    combined = _module().combine([left, right])
    assert combined["replica_identity"]["status"] == "declared-matching-fingerprint"
    assert combined["replica_identity"]["attestation"].startswith("operator-declared")


def test_combine_rejects_invalid_fingerprint_and_modern_identity_mismatch(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left, configuration_fingerprint="sha256:not-a-real-digest")
    _artifact(right, configuration_fingerprint="sha256:not-a-real-digest")
    with pytest.raises(ValueError, match="64 lowercase hex"):
        _module().combine([left, right])

    fingerprint = "sha256:" + "c" * 64
    _artifact(left, configuration_fingerprint=fingerprint, model="model-a")
    _artifact(right, configuration_fingerprint=fingerprint, model="model-b")
    with pytest.raises(ValueError, match="model values must match"):
        _module().combine([left, right])

    _artifact(left, configuration_fingerprint=fingerprint, model="model", source_recipe="a")
    _artifact(right, configuration_fingerprint=fingerprint, model="model", source_recipe="b")
    with pytest.raises(ValueError, match="source_recipe values must match"):
        _module().combine([left, right])


def test_combine_rejects_duplicate_paths_symlinks_hardlinks_digests_and_run_identity(
    tmp_path,
):
    module = _module()
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    with pytest.raises(ValueError, match="duplicate replica input"):
        module.combine([left, left])

    symlink = tmp_path / "left-symlink.json"
    try:
        symlink.symlink_to(left)
    except OSError:
        symlink = None
    if symlink is not None:
        with pytest.raises(ValueError, match="filesystem alias"):
            module.combine([left, symlink])

    hardlink = tmp_path / "left-hardlink.json"
    os.link(left, hardlink)
    with pytest.raises(ValueError, match="filesystem alias"):
        module.combine([left, hardlink])

    right.write_bytes(left.read_bytes())
    with pytest.raises(ValueError, match="distinct SHA-256"):
        module.combine([left, right])

    shared_identity = {
        "run_id": "same-run", "model": "same-model",
        "base_url": "http://127.0.0.1:30000/v1", "gpu": "same-gpu",
    }
    _artifact(left, replica_note="left", **shared_identity)
    _artifact(right, replica_note="right", **shared_identity)
    with pytest.raises(ValueError, match="composite run identity"):
        module.combine([left, right])


def test_combiner_output_safety_rejects_inputs_unrelated_files_and_wrong_suffix(
    tmp_path, monkeypatch
):
    module = _module()
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    payload = module.combine([left, right], artifact_base=tmp_path)

    with pytest.raises(ValueError, match="must not overwrite"):
        module._validate_output_target(left, [left, right], payload)
    output_symlink = tmp_path / "output-symlink.json"
    try:
        output_symlink.symlink_to(left)
    except OSError:
        output_symlink = None
    if output_symlink is not None:
        with pytest.raises(ValueError, match="symbolic link"):
            module._validate_output_target(output_symlink, [left, right], payload)
    output_hardlink = tmp_path / "output-hardlink.json"
    os.link(left, output_hardlink)
    with pytest.raises(ValueError, match="must not alias"):
        module._validate_output_target(output_hardlink, [left, right], payload)
    with pytest.raises(ValueError, match=".json suffix"):
        module._validate_output_target(tmp_path / "aggregate.txt", [left, right], payload)

    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text('{"schema": "something-else"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="non-aggregate"):
        module._validate_output_target(unrelated, [left, right], payload)
    unrelated.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-aggregate"):
        module._validate_output_target(unrelated, [left, right], payload)

    output = tmp_path / "aggregate.json"
    module._atomic_write_json(output, payload)
    module._validate_output_target(output, [left, right], payload)
    monkeypatch.setattr(
        sys, "argv",
        [str(SCRIPT), str(left), str(right), "--output", str(output)],
    )
    assert module.main() == 0
    assert b"\r\n" not in output.read_bytes()

    changed = json.loads(output.read_text(encoding="utf-8"))
    changed["replicas"][0]["sha256"] = "0" * 64
    output.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="different inputs"):
        module._validate_output_target(output, [left, right], payload)
