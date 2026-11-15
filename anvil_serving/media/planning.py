"""Compile structured shot briefs using server-owned recipes, without an LLM."""
from __future__ import annotations

from collections.abc import Mapping
from string import Formatter

from .errors import MediaError


CATEGORIES = {
    "text-to-video": "Generate a shot from text.",
    "image-to-video": "Animate an explicitly bound first/reference frame.",
    "video-to-video": "Transform footage using declared video controls.",
    "identity-replacement": "Replace explicitly mapped identities in recorded footage.",
    "audio-driven": "Drive declared subjects from bound audio inputs.",
    "camera-control": "Generate shots using explicit camera conditioning.",
    "continuation": "Extend shots using a qualified continuity recipe.",
    "spatial-upscale": "Increase spatial resolution.",
    "frame-interpolation": "Increase frame rate using temporal interpolation.",
    "image-generation": "Generate reference stills or composed scenes.",
}
BRIEF_FIELDS = ("scene", "subjects", "action", "setting", "camera", "lighting", "style", "dialogue")


def template_fields(template: str) -> tuple[str, ...]:
    if not isinstance(template, str) or len(template) > 16384:
        raise MediaError("invalid_contract", "prompt template is invalid")
    try:
        fields = []
        for _, name, spec, conversion in Formatter().parse(template):
            if name is not None:
                if name not in BRIEF_FIELDS or spec or conversion:
                    raise ValueError("unsupported prompt field")
                fields.append(name)
        return tuple(dict.fromkeys(fields))
    except ValueError as exc:
        raise MediaError("invalid_contract", "prompt template contains unsupported fields") from exc


def compile_brief(workflow, parameters: Mapping, brief: Mapping | None) -> dict:
    if not isinstance(parameters, Mapping):
        raise MediaError("invalid_parameters", "workflow parameters must be an object")
    result = dict(parameters)
    if brief is None:
        return result
    if not isinstance(brief, Mapping) or not brief or not workflow.prompt_template:
        raise MediaError("unsupported_brief", "this workflow needs a nonempty supported shot brief")
    fields = template_fields(workflow.prompt_template)
    if set(brief) - set(fields):
        raise MediaError("unsupported_brief", "shot brief contains fields unused by this recipe")
    if any(not isinstance(v, str) or len(v) > 4096 for v in brief.values()):
        raise MediaError("invalid_brief", "shot brief fields must be bounded strings")
    if "scene" in fields and not brief.get("scene", "").strip():
        raise MediaError("invalid_brief", "a scene is required by this recipe")
    if workflow.prompt_parameter in result:
        raise MediaError("prompt_conflict", "provide either a shot brief or the compiled prompt")
    # Literal dialogue is inserted verbatim; template substitution never interprets its braces.
    result[workflow.prompt_parameter] = workflow.prompt_template.format_map(
        {name: brief.get(name, "") for name in fields})
    return result


def validate_catalog(value, workflows) -> tuple[dict, ...]:
    """Catalog entries describe recipes; only registered workflows can execute."""
    if not isinstance(value, list) or len(value) > 128:
        raise MediaError("invalid_contract", "recipe catalog must be a bounded list")
    result = []; seen = set()
    for recipe in value:
        if (not isinstance(recipe, dict) or set(recipe) - {"id", "title", "category", "status", "notes", "workflow"}
                or any(not isinstance(recipe.get(k), str) or not recipe[k] or len(recipe[k]) > limit
                       for k, limit in (("id", 128), ("title", 128), ("notes", 2048)))
                or not isinstance(recipe.get("category"), str) or recipe["category"] not in CATEGORIES
                or not isinstance(recipe.get("status"), str)
                or recipe["status"] not in {"discovered", "static-reviewed", "needs-qualification", "ready", "rejected"}
                or recipe["id"] in seen):
            raise MediaError("invalid_contract", "recipe catalog entry is invalid")
        key = recipe.get("workflow")
        if key is not None and (not isinstance(key, str) or key not in workflows):
            raise MediaError("invalid_contract", "recipe names an unregistered workflow")
        if recipe["status"] == "ready" and (not key or not workflows[key].available):
            raise MediaError("invalid_contract", "ready recipe needs a qualified available workflow")
        if recipe["status"] == "ready" and recipe["category"] != workflows[key].category:
            raise MediaError("invalid_contract", "ready recipe category differs from its workflow")
        seen.add(recipe["id"]); result.append(dict(recipe))
    return tuple(result)
