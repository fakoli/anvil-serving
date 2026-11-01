

from anvil_serving.benchmarking.retention import retain_swe_artifacts


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
