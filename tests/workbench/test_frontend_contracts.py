from pathlib import Path


STATIC = Path(__file__).parents[2] / "anvil_serving" / "observability" / "dashboard" / "static"


def test_workbench_shell_uses_authenticated_owner_views_and_no_synthetic_compute_surface():
    shell = (STATIC / "observatory.js").read_text(encoding="utf-8")
    api = (STATIC / "views" / "api.js").read_text(encoding="utf-8")
    assert "workbenchRequest" in api and "api/workbench/v1/" in api
    for page in ("workbench", "playground", "models", "observability", "compute", "documentation", "settings"):
        assert page in shell
    assert "compute-surface" not in shell


def test_container_exec_is_closed_to_declared_command_ids_in_the_compute_view():
    source = (STATIC / "views" / "compute.js").read_text(encoding="utf-8")
    assert "Review diagnostic" in source
    assert "previewAction" in source
    assert "current.exec.commands" in source
    assert "docker exec" not in source and "innerHTML" not in source


def test_playground_persists_conversations_at_the_owner_and_aborts_polls():
    source = (STATIC / "views" / "playground.js").read_text(encoding="utf-8")
    assert "localStorage" not in source
    assert "conversations/" in source and "messages" in source
    assert "clearTimeout(poll)" in source


def test_project_evidence_requires_a_reviewed_digest_and_keeps_acceptance_independent():
    source = (STATIC / "views" / "project_work.js").read_text(encoding="utf-8")
    assert "Capture patch" in source
    assert "Verify & transfer reviewed patch" in source
    assert "Submit evidence to Anvil" in source
    assert "artifact_digest: digest" in source
    assert "acceptance remains a separate review" in source
    assert "innerHTML" not in source


def test_pi_chat_uses_declared_catalog_and_server_owned_session_state():
    source = (STATIC / "views" / "pi_chat.js").read_text(encoding="utf-8")
    assert 'workbenchRequest("catalog"' in source
    assert 'workbenchRequest("preferences"' in source
    assert "catalog?.pi" in source
    assert "newClaimReady" in source and "commandsAllowed" in source
    assert '["prompt", "steer"].includes(name) && result.accepted' in source
    assert 'send("steer"' in source
    assert 'type: "submit"' in source
    assert "root.replaceChildren(...children)" in source
    assert "replaceChildren(state.error ?" not in source
    assert "clearTimer();\n    const current" not in source
    assert "state.cursor = 0" not in source[source.index("const send ="):source.index("const selectSession")]
    assert "MAX_MESSAGES" in source and "message_update" in source
