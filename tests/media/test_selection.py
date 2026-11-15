"""Recipe selection is bounded planning, never backend routing or submission."""
from dataclasses import replace
import datetime as dt
import io
import json

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.mcp.tools.media import service_context
from anvil_serving.media.contracts import QualityProfile, utc_now
from anvil_serving.media.errors import MediaError
from anvil_serving.media.planning import validate_catalog
from anvil_serving.media.selection import select_workflow, validate_selection
from tests.media.test_artifact_inputs import Backend
from tests.media.test_artifacts import job, png
from tests.media.test_planning import setup


def configure(operations):
    image = operations.registry.get("image.test", "v1")
    video = replace(image, id="video.test", kind="video", category="text-to-video",
                    output_mime_types=("video/mp4",),
                    quality_profiles={"short": QualityProfile("Short", {"width": 512, "height": 384}),
                                      "long": QualityProfile("Long", {"width": 512, "height": 384})},
                    default_quality_profile="short")
    operations.registry._descriptors[video.key] = video
    operations.registry._graphs[video.key] = operations.registry._graphs[image.key]
    rows = [
        {"id": "still", "title": "Still", "notes": "Generate a still.", "category": "image-generation",
         "status": "ready", "workflow": image.key, "selection": {"tasks": ["image-create"]}},
        {"id": "preferred", "title": "Preferred", "notes": "Operator preferred video.", "category": "text-to-video",
         "status": "ready", "workflow": video.key,
         "selection": {"tasks": ["text-to-video", "segmented-video"], "priority": 0,
                       "durations": {"short": 5.0625, "long": 12.25}}},
        {"id": "alternative", "title": "Alternative", "notes": "Explicit alternative.", "category": "text-to-video",
         "status": "ready", "workflow": video.key,
         "selection": {"tasks": ["text-to-video"], "priority": 20,
                       "durations": {"short": 5.0625, "long": 12.25}}},
        {"id": "candidate", "title": "Unqualified", "notes": "Must never be selected.", "category": "text-to-video",
         "status": "needs-qualification", "workflow": video.key,
         "selection": {"tasks": ["text-to-video"], "priority": 0}},
    ]
    operations.registry._catalog = validate_catalog(rows, operations.registry._descriptors)
    return operations


def choose(operations, **kwargs):
    return select_workflow(operations, kwargs.pop("task", "text-to-video"),
        kwargs.pop("parameters", {"prompt": "Robot waves."}), principal="hermes",
        idempotency_key="select-one", **kwargs)


def test_preference_explicit_override_duration_and_no_duplicate_submission(tmp_path):
    operations = configure(setup(tmp_path))
    first = choose(operations)
    assert first["selection"]["recipeId"] == "preferred"
    assert first["selection"]["qualityProfile"] == "short"
    assert choose(operations, recipe_id="alternative")["selection"]["recipeId"] == "alternative"
    long = choose(operations, duration_seconds=12)
    assert long["selection"]["qualityProfile"] == "long"
    assert long["selection"]["durationSeconds"] == pytest.approx(12.25)
    for duration in (8, 60):
        assert choose(operations, duration_seconds=duration)["selection"]["status"] == "needs_clarification"
    assert choose(operations, quality_profile="missing")["selection"]["status"] == "needs_clarification"
    assert not operations.jobs.nonterminal() and first["jobSubmitted"] is False
    backend = Backend()
    arguments = first["runArguments"]
    assert first["plan"]["backendChecked"] is False
    assert operations.workflow_run(**arguments, principal="hermes", backend=backend)["created"]
    assert not operations.workflow_run(**arguments, principal="hermes", backend=backend)["created"]
    assert len(backend.submissions) == 1


def test_missing_inputs_ties_blocked_recipes_and_invalid_constraints(tmp_path):
    operations = configure(setup(tmp_path))
    missing = choose(operations, parameters={})
    assert missing["selection"]["missingParameters"] == ["prompt"]
    assert missing["selection"]["referencesChecked"] is False and "runArguments" not in missing
    with pytest.raises(MediaError, match="not approved"):
        choose(operations, recipe_id="candidate")
    for parameters in ({"prompt": "Robot", "model": "arbitrary"}, {"prompt": "Robot", "width": 1024}):
        with pytest.raises(MediaError):
            choose(operations, parameters=parameters)
    for value in (True, -1, float("nan"), float("inf"), 7201, "26"):
        with pytest.raises(MediaError):
            choose(operations, duration_seconds=value)
    rows = operations.registry.recipes()
    rows[2]["selection"]["priority"] = 0
    operations.registry._catalog = validate_catalog(rows, operations.registry._descriptors)
    tied = choose(operations)
    assert tied["selection"]["reason"] == "ambiguous_recipes" and "runArguments" not in tied
    assert not operations.jobs.nonterminal()


def test_selection_uses_owned_intake_and_rechecks_prepared_contract(tmp_path, monkeypatch):
    operations = configure(setup(tmp_path))
    artifact = operations.artifacts.ingest(job(), io.BytesIO(png()), media_type="image/png",
                                         max_bytes=1024, retention_seconds=60)
    parameters = {"prompt": "Robot", "first_frame": artifact.id}
    prepared = choose(operations, task="image-create", parameters=parameters)
    assert prepared["plan"]["references"][0]["role"] == "first-frame"
    with pytest.raises(MediaError, match="not found"):
        select_workflow(operations, "image-create", parameters, principal="someone-else", idempotency_key="foreign")
    monkeypatch.setattr("anvil_serving.media.artifacts.utc_now", lambda: utc_now() + dt.timedelta(minutes=2))
    with pytest.raises(MediaError, match="not found"):
        choose(operations, task="image-create", parameters=parameters)
    assert not operations.jobs.nonterminal()


def test_catalog_rejects_unbounded_or_unknown_selection_policy(tmp_path):
    operations = configure(setup(tmp_path))
    workflow = operations.registry.get("video.test", "v1")
    for value in ({"tasks": ["unknown"]}, {"tasks": ["text-to-video"] * 2},
                  {"tasks": ["text-to-video"], "priority": True},
                  {"tasks": ["text-to-video"], "durations": {"missing": 5}},
                  {"tasks": ["text-to-video"], "durations": {"short": float("nan")}},
                  {"tasks": ["image-create"]}, {"tasks": ["text-to-video"], "fallback": "cloud"}):
        with pytest.raises(MediaError):
            validate_selection(value, workflow)


def test_mcp_and_cli_share_selection_and_scope_is_checked_first(tmp_path, monkeypatch, capsys):
    from anvil_serving.media import cli
    operations = configure(setup(tmp_path))
    arguments = {"task": "text-to-video", "parameters": {"prompt": "Robot"},
                 "idempotency_key": "client-select"}
    with service_context(operations, None):
        result = mcp.call_tool("media_workflow_select", arguments,
            caller={"principal": "hermes", "scopes": ["media:read"]}, audience="media")
        assert result["ok"] and result["data"]["selection"]["recipeId"] == "preferred"
        denied = mcp.call_tool("media_workflow_select", arguments,
            caller={"principal": "hermes", "scopes": ["media:submit"]}, audience="media")
        assert denied["error"]["code"] == "scope_denied"
        spoof = mcp.call_tool("media_workflow_select", {**arguments, "owner": "someone-else"},
            caller={"principal": "hermes", "scopes": ["media:read"]}, audience="media")
        assert spoof["error"]["code"] == "bad_argument"
    monkeypatch.setattr(cli, "_operations", lambda _: operations)
    assert cli.main(["workflow", "select", "--task", "text-to-video", "--principal", "hermes",
                     "--idempotency-key", "client-select", "--parameters", '{"prompt":"Robot"}']) == 0
    assert json.loads(capsys.readouterr().out) == result["data"]
    assert not operations.jobs.nonterminal()
