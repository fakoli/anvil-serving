import hashlib

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
