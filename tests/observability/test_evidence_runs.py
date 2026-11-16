from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

try:
    import resource
except ImportError:  # pragma: no cover - Windows does not expose POSIX descriptor limits.
    resource = None

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError, canonical
from anvil_serving.observability.dashboard.evidence_runs import EvidenceRuns
from anvil_serving.benchmarking.artifacts import build_external_prior_record

pytestmark = pytest.mark.skipif(os.name != "posix", reason="retained catalog requires safe POSIX descriptor reads")


def _artifact(*, run_id="same-run", model="fixture", failed=False):
    return {
        "schema": "anvil-serving.benchmark/v1",
        "run_id": run_id,
        "identity": {"model": model},
        "requests": 2,
        "completed": 0 if failed else 2,
        "concurrency": 1,
        "metrics": {"throughput_tok_s": 12.5},
    }


def _write(path: Path, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_artifact(**kwargs)), encoding="utf-8")


def _capacity(*, concurrency=1, missing=False):
    value = _artifact(model="capacity")
    value.update({
        "requests": 4, "completed": 4, "concurrency": concurrency,
        "context_tokens": 1024, "max_context_tokens": 4096, "max_tokens": 128,
        "engine": "fixture-engine", "gpu": "fixture-gpu", "cache_policy": "warm",
        "prompt_set_id": "fixture-prompts", "sampling": {
            "temperature": {"requested": 0.0, "effective_request": 0.0, "sent": True},
            "top_p": {"requested": 1.0, "effective_request": 1.0, "sent": True},
        },
        "serve_flags": {"thinking_mode": "default", "no_thinking": False,
                         "shared_prefix_burst": False},
    })
    if missing:
        value.pop("max_tokens")
    return value


def _refs(source, root):
    page = source.page(limit=100, authority_key="reader")
    return [row["evidence_refs"][0] for row in page["items"]]


def _source(root: Path, *, clock=time.monotonic, worker_target=None):
    kwargs = {"clock": clock}
    if worker_target is not None:
        kwargs["worker_target"] = worker_target
    return EvidenceRuns({
        "root": str(root), "owner_id": "evidence-owner",
        "resource_id": "evaluation.evidence",
    }, **kwargs)


def _all(source: EvidenceRuns, authority="reader"):
    cursor = None
    rows = []
    while True:
        page = source.page(limit=100, cursor=cursor, authority_key=authority)
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return rows


def test_page_reserves_full_worker_envelope_before_skipping_records(tmp_path, monkeypatch):
    from anvil_serving.observability.dashboard import evidence_runs as owner

    # Keep one row just below the former inner-page limit, then grow ignored
    # metadata after it. The actual serialized worker result is the contract.
    monkeypatch.setattr(owner, "MAX_RESULT_BYTES", 1024)
    records = [{"id": str(index), "f": [0, 0, 1, 0]} for index in range(101)]
    row = {"id": "row", "padding": "x" * 868}
    monkeypatch.setattr(owner, "_row", lambda *_args: row)
    monkeypatch.setattr(owner, "_read_artifact", lambda _root, record, _deadline:
                        ({}, "available", "a" * 64, 1) if record["id"] == "0"
                        else (None, "unrecognized", None, 1))
    page = owner._page(str(tmp_path), records, 0, 100, "owner", "resource", time.monotonic() + 1)
    assert len(canonical({"ok": True, "page": page})) <= owner.MAX_RESULT_BYTES
    assert page["next_index"] == 0  # The rejected row stays available to a larger bounded page.

    # Admit a slightly smaller row and consume all following ignored records.
    row["padding"] = "x" * 800
    page = owner._page(str(tmp_path), records, 0, 100, "owner", "resource", time.monotonic() + 1)
    assert len(page["items"]) == 1 and page["flags"]["unrecognized"] == 100
    result = tmp_path / "result.json"
    result.touch()
    owner._write_worker_result(str(result), {"ok": True, "page": page})
    assert owner._read_worker_result(str(result), owner.MAX_RESULT_BYTES)["page"] == page


def test_cursor_is_bound_to_authority_and_explicitly_expires(tmp_path):
    root = tmp_path / "findings"
    _write(root / "one.json")
    now = [10.0]
    source = _source(root, clock=lambda: now[0])
    page = source.page(limit=1, authority_key="allowed")
    cursor = page["next_cursor"] or "e1." + next(iter(source._snapshots)) + ".0"
    with pytest.raises(ObservatoryError) as denied:
        source.page(limit=1, cursor=cursor, authority_key="other")
    assert denied.value.code == "forbidden"
    now[0] += 61
    with pytest.raises(ObservatoryError) as expired:
        source.page(limit=1, cursor=cursor, authority_key="allowed")
    assert expired.value.code == "run_cursor_expired"
    source.close()


def test_safe_open_rejects_symlink_fifo_and_replaced_file(tmp_path):
    root = tmp_path / "findings"
    _write(root / "safe.json")
    _write(root / "replace.json")
    _write(root / ".env.json", model="secret")
    _write(root / "secrets" / "private.json", model="secret")
    os.symlink(root / "safe.json", root / "link.json")
    os.mkfifo(root / "pipe.json")
    source = _source(root)
    source.page(limit=1, authority_key="reader")
    snapshot = next(iter(source._snapshots.values()))
    (root / "replace.json").unlink()
    (root / "replace.json").write_text(json.dumps(_artifact(model="different")), encoding="utf-8")
    changed = source.page(
        limit=10, cursor=source._cursor_text(snapshot, 0), authority_key="reader",
    )
    fresh = source.page(limit=10, authority_key="new")
    assert {item["model"] for item in changed["items"]} == {"fixture"}
    assert changed["sources"][0]["partial"] is True
    assert {item["model"] for item in fresh["items"]} == {"fixture", "different"}
    assert fresh["sources"][0]["partial"] is False
    source.close()


def test_external_prior_is_never_projected_as_locally_measured(tmp_path):
    root = tmp_path / "findings"
    root.mkdir()
    (root / "prior.json").write_text(json.dumps(build_external_prior_record(
        source={"url": "https://example.test/benchmark", "observed_at": "2026-09-19"},
        claims={"score": 1},
    )), encoding="utf-8")
    source = _source(root)
    row = source.page(authority_key="reader")["items"][0]
    assert row["evidence_kind"] == "external_prior"
    assert row["locally_measured"] is False
    source.close()

    (root / "measured.json").write_text(json.dumps(_capacity()), encoding="utf-8")
    source = _source(root)
    with pytest.raises(ObservatoryError) as rejected:
        source.compare(refs=_refs(source, root), authority_key="reader")
    assert rejected.value.code == "evidence_ineligible"
    source.close()


def test_detail_and_compare_are_digest_bound_and_compact(tmp_path):
    root = tmp_path / "findings"
    first, second = root / "first.json", root / "second.json"
    first.parent.mkdir()
    first.write_text(json.dumps(_capacity()), encoding="utf-8")
    second_payload = _capacity()
    second_payload["run_id"] = "distinct-retained-run"
    second.write_text(json.dumps(second_payload), encoding="utf-8")
    source = _source(root)
    refs = _refs(source, root)
    first_ref = next(
        ref for ref in refs
        if ref["artifact_id"] == "artifact-" + hashlib.sha256(b"first.json").hexdigest()
    )
    detail = source.detail(
        artifact_id=refs[0]["artifact_id"], sha256=refs[0]["sha256"], authority_key="reader",
    )
    compared = source.compare(refs=refs, authority_key="reader")
    assert detail["summary"]["capacity"]["concurrency"] == 1
    assert detail["observed_at"]
    assert "path" not in repr(detail) and "provenance" not in repr(detail)
    assert compared["comparable"] is True
    assert compared["differences"] == []
    assert compared["unknown_fields"] == []
    assert compared["invalid_artifacts"] == []
    assert compared["observed_at"]
    assert all(row["observed_at"] == compared["observed_at"] for row in compared["artifacts"])
    with pytest.raises(ObservatoryError) as changed:
        source.detail(artifact_id=refs[0]["artifact_id"], sha256=refs[1]["sha256"], authority_key="reader")
    assert changed.value.code == "evidence_changed"
    mutated = _capacity()
    mutated["run_id"] = "changed-after-listing"
    first.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ObservatoryError) as changed_content:
        source.detail(artifact_id=first_ref["artifact_id"], sha256=first_ref["sha256"], authority_key="reader")
    assert changed_content.value.code == "evidence_changed"
    source.close()


def _hung_worker(_path, _job):
    time.sleep(10)
