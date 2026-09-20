from __future__ import annotations

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


def test_retained_artifacts_paginate_stably_after_new_file_and_identical_run_ids(tmp_path):
    root = tmp_path / "findings"
    for number in range(101):
        path = root / f"item-{number:03d}.json"
        _write(path, run_id="duplicate", model=f"model-{number}", failed=number == 100)
        os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    source = _source(root)
    first = source.page(limit=100, authority_key="reader")
    _write(root / "new-after-snapshot.json", run_id="duplicate", model="new")
    second = source.page(limit=100, cursor=first["next_cursor"], authority_key="reader")

    rows = [*first["items"], *second["items"]]
    assert len(rows) == 101
    assert len({item["id"] for item in rows}) == 101
    assert {item["run_id"] for item in rows} == {"duplicate"}
    assert any(item["model"] == "model-100" for item in rows)
    assert not any(item["model"] == "new" for item in rows)
    assert all(item["source"] == "evidence" and item["kind"] == "imported" for item in rows)
    assert all(item["observed_at"] == first["sources"][0]["observed_at"] for item in first["items"])
    assert len(canonical(first)) <= 128 * 1024
    source.close()


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


def test_changed_deleted_oversize_and_malformed_artifacts_are_partial_not_rows(tmp_path):
    root = tmp_path / "findings"
    _write(root / "valid.json")
    _write(root / "changed.json")
    (root / "malformed.json").write_text("{", encoding="utf-8")
    (root / "oversize.json").write_bytes(b"{" + b" " * (32 * 1024 * 1024))
    source = _source(root)
    first = source.page(limit=1, authority_key="reader")
    (root / "changed.json").write_text(json.dumps(_artifact(model="replaced")), encoding="utf-8")
    rows = [*first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = source.page(limit=10, cursor=cursor, authority_key="reader")
        rows.extend(page["items"])
        cursor = page["next_cursor"]
    final = source.page(limit=10, authority_key="fresh")

    assert {row["model"] for row in rows} == {"fixture"}
    assert final["sources"][0]["partial"] is True
    assert final["sources"][0]["unavailable_artifacts"] == 1
    assert final["sources"][0]["ignored_json"] == 1
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


@pytest.mark.skipif(resource is None, reason="POSIX descriptor limits are unavailable")
def test_wide_catalog_does_not_accumulate_scan_descriptors(tmp_path):
    root = tmp_path / "findings"
    for number in range(140):
        _write(root / f"directory-{number:03d}" / "artifact.json", model=str(number))
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard < 64:
        pytest.skip("fixture requires a 64-descriptor limit")
    source = _source(root)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
        page = source.page(limit=100, authority_key="reader")
        rows = [*page["items"]]
        while page["next_cursor"]:
            page = source.page(limit=100, cursor=page["next_cursor"], authority_key="reader")
            rows.extend(page["items"])
        assert len(rows) == 140
        assert page["sources"][0]["partial"] is False
        assert page["sources"][0]["truncated"] is False
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
        source.close()


def _hung_worker(_path, _job):
    time.sleep(10)


def test_hung_worker_is_killed_and_source_releases_its_slot(tmp_path):
    root = tmp_path / "findings"
    _write(root / "one.json")
    source = _source(root, worker_target=_hung_worker)
    started = time.monotonic()
    with pytest.raises(ObservatoryError) as error:
        source.page(authority_key="reader")
    assert error.value.code == "owner_unavailable"
    assert time.monotonic() - started <= 2.0
    assert source._active is None
    source.close()


def test_real_representative_artifact_is_normalized_without_exposing_its_path():
    root = Path(__file__).parents[2] / "docs" / "findings"
    source = _source(root)
    rows = _all(source)
    row = next(item for item in rows if item["run_id"] == "benchmark-20260919T112856Z")
    assert row["model"] == "glm53-flash-exl3-4bpw-v84-fp8-tp2-c4-327k-r10-vision8-apc-nospec"
    assert row["locally_measured"] is True
    assert "/" not in row["native_id"]
    assert "raw-run-evidence" not in repr(row)
    source.close()
