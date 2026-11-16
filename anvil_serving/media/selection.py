"""Select an approved operator recipe from a harness-interpreted media request."""
from __future__ import annotations

from collections.abc import Mapping
import math

from .errors import MediaError
from .planning import compile_brief


TASKS = (
    "image-create", "image-edit", "text-to-video", "image-to-video", "keyframe-video",
    "reference-video", "audio-reference-video", "character-mask", "character-swap",
    "segmented-video", "video-upscale",
)


def validate_selection(value, workflow):
    if (not isinstance(value, dict) or set(value) - {"tasks", "priority", "durations"}
            or not isinstance(value.get("tasks"), list) or not value["tasks"]
            or len(value["tasks"]) > len(TASKS)
            or any(not isinstance(task, str) or task not in TASKS for task in value["tasks"])
            or len(set(value["tasks"])) != len(value["tasks"])):
        raise MediaError("invalid_contract", "recipe selection tasks are invalid")
    priority = value.get("priority", 50)
    durations = value.get("durations", {})
    if type(priority) is not int or not 0 <= priority <= 1000:
        raise MediaError("invalid_contract", "recipe selection priority is invalid")
    profiles = set(workflow.quality_profiles) or {""}
    if (not isinstance(durations, dict) or set(durations) - profiles
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 7200
                   for v in durations.values())):
        raise MediaError("invalid_contract", "recipe selection durations must name bounded profiles")
    if any((task.startswith("image-") and task != "image-to-video") != (workflow.kind == "image")
           for task in value["tasks"]):
        raise MediaError("invalid_contract", "recipe selection task differs from its output kind")


def select_workflow(operations, task, parameters, *, principal, idempotency_key,
                    recipe_id="", quality_profile="", duration_seconds=None, brief=None):
    """Return a choice or the existing digest-bound plan; never contact a worker."""
    if not isinstance(task, str) or task not in TASKS:
        raise MediaError("invalid_selection", "interpret the request as one supported media task",
                         details={"tasks": list(TASKS)})
    if (not isinstance(parameters, Mapping) or len(parameters) > 32
            or not isinstance(recipe_id, str) or len(recipe_id) > 128
            or not isinstance(quality_profile, str) or len(quality_profile) > 128
            or not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128):
        raise MediaError("invalid_selection", "selection arguments exceed the bounded contract")
    if duration_seconds is not None and (
            type(duration_seconds) not in (int, float) or not math.isfinite(duration_seconds)
            or not 0 < duration_seconds <= 7200):
        raise MediaError("invalid_selection", "requested duration must be a finite positive number")
    recipes = [r for r in operations.registry.recipes()
               if r["status"] == "ready" and task in r.get("selection", {}).get("tasks", [])]
    if recipe_id:
        recipes = [r for r in recipes if r["id"] == recipe_id]
        if not recipes:
            raise MediaError("media_recipe_unavailable", "the requested recipe is not approved for this task")
    choices = []
    candidates = []
    for recipe in recipes:
        workflow_id, version = recipe["workflow"].rsplit("@", 1)
        workflow = operations.registry.get(workflow_id, version)
        if not workflow.available:
            continue
        policy = recipe["selection"]
        profiles = workflow.quality_profiles or {"": None}
        for profile in profiles:
            duration = policy.get("durations", {}).get(profile)
            choice = {"recipeId": recipe["id"], "workflowId": workflow_id, "version": version,
                      "qualityProfile": profile, "durationSeconds": duration}
            choices.append(choice)
            if quality_profile and profile != quality_profile:
                continue
            if duration_seconds is None and not quality_profile and profile != workflow.default_quality_profile:
                continue
            delta = abs(duration - duration_seconds) if duration is not None and duration_seconds is not None else 0
            # shortcut: fixed profiles allow whole-second rounding, never extend an execution bound.
            if duration_seconds is not None and (duration is None or delta > 0.5):
                continue
            candidates.append(((delta, policy.get("priority", 50)), choice, recipe, workflow))
    if not candidates:
        return {"selection": {"status": "needs_clarification", "reason": "no_approved_match",
                              "task": task, "choices": choices}, "jobSubmitted": False}
    candidates.sort(key=lambda item: item[0])
    best = candidates[0]
    if len(candidates) > 1 and best[0] == candidates[1][0]:
        return {"selection": {"status": "needs_clarification", "reason": "ambiguous_recipes",
                              "task": task, "choices": [c[1] for c in candidates if c[0] == best[0]]},
                "jobSubmitted": False}
    _, choice, recipe, workflow = best
    compiled = compile_brief(workflow, parameters, brief)
    profile_parameters = workflow.quality_profiles[choice["qualityProfile"]].parameters if choice["qualityProfile"] else {}
    unknown = sorted(set(compiled) - set(workflow.parameters))
    overrides = sorted(set(compiled) & set(profile_parameters))
    if unknown or overrides:
        raise MediaError("invalid_selection_parameters", "selected recipe inputs are unknown or profile-owned",
                         details={"unknown": unknown, "profileOwned": overrides})
    for name, value in compiled.items():
        workflow.parameters[name].validate(name, value)
    missing = sorted(name for name, spec in workflow.parameters.items()
                     if spec.required and name not in compiled and name not in profile_parameters)
    result = {"selection": {**choice, "task": task, "status": "needs_inputs" if missing else "prepared",
                            "notes": recipe["notes"], "missingParameters": missing,
                            "workflow": workflow.as_public_dict()}, "jobSubmitted": False}
    if missing:
        result["selection"]["referencesChecked"] = False
        return result
    result.update(operations.workflow_prepare(choice["workflowId"], choice["version"], compiled,
        principal=principal, idempotency_key=idempotency_key, quality_profile=choice["qualityProfile"]))
    result["nextTool"] = "media_workflow_run"
    return result
