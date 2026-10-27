"""The recipe compiler preserves content and binds preparation to real execution."""
from dataclasses import replace
import datetime as dt
import io
import json

import pytest

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.contracts import utc_now
from anvil_serving.media.errors import MediaError
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.operations import MediaOperations
from anvil_serving.media.planning import template_fields
from anvil_serving.media.workflows import WorkflowRegistry, canonical_digest
from tests.media.test_artifact_inputs import Backend
from tests.media.test_artifacts import job, png
from tests.media.test_image_workflow_integration import _registry


def setup(tmp_path):
    _registry(tmp_path, profiled=True)
    path = tmp_path / "descriptor.json"
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    graph = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    graph["1"]["inputs"]["image"] = ""
    descriptor.update(category="image-generation", description="Synthetic reference-still recipe.",
                      prompt_template="{scene}\nCamera: {camera}\nDialogue: {dialogue}",
                      available=True, unavailable_reasons=[], graph_digest=canonical_digest(graph))
    descriptor["parameters"]["first_frame"] = {"kind": "artifact", "required": False,
        "media_types": ["image/png"], "role": "first-frame", "description": "Explicit initial frame."}
    descriptor["bindings"].append({"parameter": "first_frame", "node": "1", "input": "image"})
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    (tmp_path / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return MediaOperations(WorkflowRegistry(tmp_path / "registry.json"),
                           MediaJobStore(tmp_path / "jobs.sqlite3"), ArtifactStore(tmp_path / "artifacts"))


def test_prepare_preserves_literal_dialogue_and_binds_one_idempotent_submission(tmp_path):
    operations = setup(tmp_path); backend = Backend()
    dialogue = 'A: "世界 {cut}" B: "Yes."'
    prepared = operations.workflow_prepare("image.test", "v1", {}, principal="hermes",
        idempotency_key="shot-one", brief={"scene": "Three hosts.", "camera": "Wide.", "dialogue": dialogue})
    arguments = prepared["runArguments"]
    assert dialogue in arguments["parameters"]["prompt"]
    assert prepared["plan"]["backendChecked"] is False and not operations.jobs.nonterminal()
    assert prepared["plan"]["effectiveParameters"]["width"] == 512
    assert operations.capabilities()["categories"][9]["workflows"] == ["image.test@v1"]
    changed = {**arguments, "parameters": {"prompt": "different"}}
    with pytest.raises(MediaError, match="prepared inputs"):
        operations.workflow_run(**changed, principal="hermes", backend=backend)
    assert not backend.submissions
    first = operations.workflow_run(**arguments, principal="hermes", backend=backend)
    again = operations.workflow_run(**arguments, principal="hermes", backend=backend)
    assert first["created"] and not again["created"] and len(backend.submissions) == 1
    assert backend.submissions[0].graph["1"]["inputs"]["prompt"].endswith(dialogue)
    with pytest.raises(MediaError, match="prepared inputs"):
        operations.workflow_run(**arguments, principal="someone-else", backend=backend)
    descriptor = operations.registry.get("image.test", "v1")
    for change in ({"description": "Revised contract."}, {"service_target": "other-worker"},
                   {"required_models": ("different.safetensors",)}, {"output_nodes": ("999",)}):
        operations.registry._descriptors[descriptor.key] = replace(descriptor, **change)
        with pytest.raises(MediaError, match="prepared inputs"):
            operations.workflow_run(**arguments, principal="hermes", backend=backend)


def test_prepare_validates_reference_role_owner_and_expiry_again_at_execution(tmp_path, monkeypatch):
    operations = setup(tmp_path); backend = Backend()
    artifact = operations.artifacts.ingest(job(), io.BytesIO(png()), media_type="image/png",
        max_bytes=1024, retention_seconds=60)
    prepared = operations.workflow_prepare("image.test", "v1", {"prompt": "Host", "first_frame": artifact.id},
        principal="hermes", idempotency_key="reference-shot")
    assert prepared["plan"]["references"][0]["role"] == "first-frame"
    with pytest.raises(MediaError, match="not found"):
        operations.workflow_prepare("image.test", "v1", {"prompt": "Host", "first_frame": artifact.id},
            principal="other", idempotency_key="other-shot")
    monkeypatch.setattr("anvil_serving.media.artifacts.utc_now", lambda: utc_now() + dt.timedelta(minutes=2))
    with pytest.raises(MediaError, match="not found"):
        operations.workflow_run(**prepared["runArguments"], principal="hermes", backend=backend)
    assert not backend.submissions


@pytest.mark.parametrize("brief,parameters", [({"negative_prompt": "blur"}, {}),
    ({"scene": "Hosts"}, {"prompt": "duplicate"}), ({"scene": "Hosts"}, {"width": 999}),
    ({"scene": "Hosts"}, {"node": "1"}), ({"scene": 1}, {}), ({"scene": ""}, {}),
    ({"scene": "Hosts", "dialogue": chr(0xD800)}, {})])
def test_unsupported_fields_and_profile_overrides_fail_before_submission(tmp_path, brief, parameters):
    operations = setup(tmp_path)
    with pytest.raises(MediaError):
        operations.workflow_prepare("image.test", "v1", parameters, brief=brief,
                                     principal="hermes", idempotency_key="invalid-shot")
    assert not operations.jobs.nonterminal()


@pytest.mark.parametrize("template", ["{scene.__class__}", "{scene[0]}", "{scene!r}", "{scene:>10}", "{unknown}", "{"])
def test_operator_templates_reject_expression_and_format_escape_hatches(template):
    with pytest.raises(MediaError):
        template_fields(template)


def test_catalog_separates_reviewed_recipes_from_executable_qualified_workflows(tmp_path):
    from anvil_serving.media.planning import validate_catalog
    operations = setup(tmp_path)
    descriptors = operations.registry._descriptors
    recipe = {"id": "reference-shot", "title": "Reference shot", "category": "image-to-video",
              "status": "static-reviewed", "notes": "Source graph parsed; runtime qualification pending."}
    assert validate_catalog([recipe], descriptors)[0]["status"] == "static-reviewed"
    assert validate_catalog([{**recipe, "category": "image-generation", "status": "ready", "workflow": "image.test@v1"}], descriptors)
    for invalid in ([{**recipe, "status": "ready"}], [recipe, recipe],
                    [{**recipe, "category": []}], [{**recipe, "workflow": "missing@v1"}],
                    [{**recipe, "status": "ready", "workflow": "image.test@v1"}]):
        with pytest.raises(MediaError):
            validate_catalog(invalid, descriptors)
    (tmp_path / "catalog.json").write_text(json.dumps([recipe]), encoding="utf-8")
    path = tmp_path / "registry.json"; raw = json.loads(path.read_text(encoding="utf-8"))
    raw["catalog"] = "catalog.json"; path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = WorkflowRegistry(path)
    loaded.recipes()[0]["status"] = "ready"
    assert loaded.recipes()[0]["status"] == "static-reviewed"


def test_native_mcp_preparation_and_exact_submission_share_the_recipe_compiler(tmp_path):
    from anvil_serving import mcp
    from anvil_serving.control_plane.mcp.tools.media import service_context
    from tests.a2a.test_tasks import Backend as McpBackend, CALLER
    operations = setup(tmp_path)
    with service_context(operations, McpBackend()):
        prepared = mcp.call_tool("media_workflow_prepare", {"workflow_id": "image.test", "version": "v1",
            "brief": {"scene": "Hosts", "camera": "Wide", "dialogue": "A: Hello."},
            "idempotency_key": "mcp-shot"}, caller=CALLER, audience="media")
        assert prepared["ok"] and not operations.jobs.nonterminal()
        submitted = mcp.call_tool("media_workflow_run", prepared["data"]["runArguments"],
                                   caller=CALLER, audience="media")
        assert submitted["ok"] and submitted["data"]["job"]["state"] == "queued"


def test_cli_prepares_the_same_request_and_rejects_invalid_values_before_auto_key(tmp_path, monkeypatch, capsys):
    from anvil_serving.media import cli
    operations = setup(tmp_path)
    monkeypatch.setattr(cli, "_operations", lambda _: operations)
    assert cli.main(["workflow", "prepare", "image.test", "--version", "v1",
        "--principal", "hermes", "--idempotency-key", "cli-shot",
        "--brief", '{"scene":"Hosts","camera":"Wide"}']) == 0
    assert json.loads(capsys.readouterr().out)["runArguments"]["plan_digest"]
    assert cli.main(["workflow", "run", "image.test", "--version", "v1",
        "--principal", "hermes", "--backend-url", "http://127.0.0.1:8188",
        "--parameters", '{"prompt":NaN}']) == 1
    assert "Traceback" not in capsys.readouterr().err and not operations.jobs.nonterminal()
