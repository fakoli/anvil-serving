import hashlib

import pytest

from anvil_serving.benchmarking.retention import retain_swe_artifacts


def test_retention_publishes_bounded_canonical_snapshot(tmp_path):
    run = tmp_path / "run"
    work = run / "work"
    (work / "mini-output" / "case").mkdir(parents=True)
    (work / "swe-plan.json").write_text('{"plan":true}\n', encoding="utf-8")
    (work / "anvil-swe-config.yaml").write_text("model: fixture\n", encoding="utf-8")
    (work / "swe-prompt-contract.yaml").write_text("agent: fixture\n", encoding="utf-8")
    (work / "predictions.jsonl").write_text('{"instance_id":"case"}\n', encoding="utf-8")
    (work / "mini-output" / "case" / "case.traj.json").write_text("{}\n", encoding="utf-8")

    retained = retain_swe_artifacts(run_path=str(run), work_path=str(work))

    assert retained["state"] == "incomplete"
    manifest = retained["manifest"]
    assert retained["manifest_reference"]["path"] == "evidence/swe-canonical/manifest.json"
    assert retained["manifest_reference"]["sha256"] == hashlib.sha256(
        (run / "evidence" / "swe-canonical" / "manifest.json").read_bytes()
    ).hexdigest()
    assert "official-grader" in manifest["missing"]
    assert "swe-prompt-contract.yaml" in {entry["path"] for entry in manifest["files"]}
    for entry in manifest["files"]:
        content = (run / "evidence" / "swe-canonical" / entry["path"]).read_bytes()
        assert entry["bytes"] == len(content)
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert retain_swe_artifacts(run_path=str(run), work_path=str(work))["reused"] is True


def test_retention_rejects_symlink_without_removing_work(tmp_path):
    run = tmp_path / "run"
    work = run / "work"
    work.mkdir(parents=True)
    (work / "predictions.jsonl").symlink_to(tmp_path / "outside")

    retained = retain_swe_artifacts(run_path=str(run), work_path=str(work))

    assert retained["state"] == "failed"
    assert work.exists()
    assert not (run / "evidence" / "swe-canonical").exists()


def _canonical_work(run, **plan_options):
    import json

    work = run / "work"
    (work / "mini-output" / "case").mkdir(parents=True)
    (work / "official-grader").mkdir()
    (work / "swe-plan.json").write_text(json.dumps({"run_id": "run", "prompt_contract": "legacy/v1", "paired_image_ids": None, **plan_options}))
    (work / "anvil-swe-config.yaml").write_text("model: fixture\n")
    (work / "predictions.jsonl").write_text('{"instance_id":"case"}\n')
    (work / "mini-output" / "case" / "case.traj.json").write_text("{}\n")
    (work / "official-grader" / "model.run.json").write_text('{"completed_ids":["case"],"resolved_ids":[],"error_ids":[]}\n')
    return work


def test_legacy_unpaired_retention_requires_only_applicable_artifacts(tmp_path):
    from anvil_serving.benchmarking.retention import read_retained_swe_artifacts

    run = tmp_path / "run"
    work = _canonical_work(run)
    retained = retain_swe_artifacts(run_path=str(run), work_path=str(work))
    assert retained["state"] == "retained"
    assert retained["manifest"]["missing"] == []
    assert read_retained_swe_artifacts(run_path=str(run)) == retained["manifest"]


@pytest.mark.parametrize(("options", "required"), [
    ({"prompt_contract": "disclosed/v2"}, "swe-prompt-contract.yaml"),
    ({"paired_image_ids": {"case": "sha256:" + "a" * 64}}, "paired-image-ids.json"),
])
@pytest.mark.parametrize("present", [False, True])
def test_retention_derives_conditional_artifacts_from_plan(tmp_path, options, required, present):
    run = tmp_path / "run"
    work = _canonical_work(run, **options)
    if present:
        (work / required).write_text("{}\n")
    retained = retain_swe_artifacts(run_path=str(run), work_path=str(work))
    assert retained["state"] == ("retained" if present else "incomplete")
    assert retained["manifest"]["missing"] == ([] if present else [required])
    assert work.exists()


@pytest.mark.parametrize("missing", ["predictions.jsonl", "mini-output", "official-grader"])
def test_empty_directories_do_not_prove_canonical_completeness(tmp_path, missing):
    run = tmp_path / "run"
    work = _canonical_work(run)
    target = work / missing
    if target.is_dir():
        for child in target.rglob("*"):
            if child.is_file():
                child.unlink()
        if missing == "official-grader":
            (target / "containment.json").write_text("{}\n")
            (target / "wrong-run.json").write_text("{}\n")
    else:
        target.unlink()
    retained = retain_swe_artifacts(run_path=str(run), work_path=str(work))
    assert retained["state"] == "incomplete"
    assert retained["manifest"]["missing"] == [missing]
    assert work.exists()


@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.parametrize("bound", ["files", "bytes", "directories", "depth"])
def test_retention_enforces_exact_resource_bounds_and_preserves_work(tmp_path, bound, extra):
    from anvil_serving.benchmarking import retention

    run = tmp_path / "run"
    work = _canonical_work(run)
    if bound == "files":
        for index in range(retention.MAX_SWE_RETAINED_FILES - 5 + extra):
            (work / "mini-output" / f"extra-{index}").touch()
    elif bound == "bytes":
        total = sum(path.stat().st_size for path in work.rglob("*") if path.is_file())
        with (work / "mini-output" / "padding").open("wb") as handle:
            handle.truncate(retention.MAX_SWE_RETAINED_BYTES - total + extra)
    elif bound == "directories":
        # Both trees share one cap, including their two roots and the case directory.
        for index in range(retention.MAX_SWE_RETAINED_DIRECTORIES - 3 + extra):
            parent = work / ("mini-output" if index % 2 else "official-grader")
            (parent / f"empty-{index}").mkdir()
    else:
        nested = work / "official-grader"
        for _ in range(retention.MAX_SWE_RETAINED_DEPTH + extra):
            nested /= "nested"
        nested.mkdir(parents=True)
    retained = retain_swe_artifacts(run_path=str(run), work_path=str(work))
    assert retained["state"] == ("failed" if extra else "retained"), retained
    assert work.exists()
    assert (work / "predictions.jsonl").read_text() == '{"instance_id":"case"}\n'
    assert (run / "evidence" / "swe-canonical").exists() is (not extra)
    assert not list((run / "evidence").glob(".swe-canonical-*"))


def test_unreadable_retained_bytes_fail_with_typed_error(tmp_path, monkeypatch):
    from anvil_serving.benchmarking import retention

    run = tmp_path / "run"
    work = _canonical_work(run)
    retain_swe_artifacts(run_path=str(run), work_path=str(work))
    monkeypatch.setattr(retention, "_digest", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("unreadable")))
    with pytest.raises(ValueError, match="canonical_manifest_unreadable"):
        retention.read_retained_swe_artifacts(run_path=str(run))
    assert work.exists()
