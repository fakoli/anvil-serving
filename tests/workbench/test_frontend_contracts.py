from pathlib import Path


STATIC = Path(__file__).parents[2] / "anvil_serving" / "observability" / "dashboard" / "static"


def test_workbench_shell_uses_authenticated_owner_views_and_no_synthetic_compute_surface():
    shell = (STATIC / "observatory.js").read_text(encoding="utf-8")
    api = (STATIC / "views" / "api.js").read_text(encoding="utf-8")
    assert "workbenchRequest" in api and "api/workbench/v1/" in api
    for page in ("workbench", "playground", "models", "observability", "compute", "documentation", "settings"):
        assert page in shell
    assert "compute-surface" not in shell


def test_project_evidence_requires_a_reviewed_digest_and_keeps_acceptance_independent():
    source = (STATIC / "views" / "project_work.js").read_text(encoding="utf-8")
    assert "Capture patch" in source
    assert "Verify & transfer reviewed patch" in source
    assert "Submit evidence to Anvil" in source
    assert "artifact_digest: digest" in source
    assert "acceptance remains a separate review" in source
    assert "innerHTML" not in source
