"""Explicit models MCP tool family."""

from __future__ import annotations

import os
import sys
import copy

from ..arguments import (
    arg_bool as _arg_bool,
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    schema as _schema,
    str_arg as _str_arg,
    str_list_arg as _str_list_arg,
)
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..runtime import (
    run_argv as _run_argv,
)


def tool_models_inventory(args: dict) -> dict:
    from .... import models, paths

    catalog_dir = _str_arg(
        args, "catalog_dir", os.path.join(paths.config_home(), "model-library")
    )
    hf_roots = _str_arg(args, "hf_roots", "")
    model_dirs = _str_arg(args, "model_dirs", "")
    sync = _arg_bool(args.get("sync"), False, name="sync")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 1800, min_value=1, max_value=7200)
    argv = models.build_sync_argv(catalog_dir, hf_roots=hf_roots, model_dirs=model_dirs)
    if sync:
        if not confirm:
            return _ok(
                {
                    "synced": False,
                    "dry_run": True,
                    "catalog_dir": os.path.abspath(catalog_dir),
                    "command": [*argv, "--dry-run"],
                }
            )
        apply_argv = [*argv, "--confirm"]
        run_result = _run_argv(apply_argv, confirm=True, timeout=timeout_seconds)
        try:
            inventory = models.load_model_catalog(catalog_dir)
        except models.CatalogNotFound as exc:
            raise ToolError(
                "catalog_not_found",
                "models sync completed but no catalog was found; check sync output and --out",
                {
                    "catalog_dir": exc.catalog_dir,
                    "command": apply_argv,
                    "stdout": run_result.get("stdout", ""),
                    "stderr": run_result.get("stderr", ""),
                },
            )
        except models.CatalogError as exc:
            raise ToolError("bad_catalog", str(exc), exc.details)
        return _ok(
            {
                "synced": True,
                "dry_run": False,
                "command": apply_argv,
                "returncode": run_result["returncode"],
                "stdout": run_result["stdout"],
                "stderr": run_result["stderr"],
                "catalog": inventory,
            }
        )

    try:
        inventory = models.load_model_catalog(catalog_dir)
    except models.CatalogNotFound as exc:
        raise ToolError(
            "catalog_not_found",
            "model catalog not found; run the command from error.details.command first",
            {"catalog_dir": exc.catalog_dir, "command": [*argv, "--confirm"]},
        )
    except models.CatalogError as exc:
        raise ToolError("bad_catalog", str(exc), exc.details)
    return _ok({"synced": False, "dry_run": False, "catalog": inventory})


def tool_model_cache_inventory(args: dict) -> dict:
    from .... import models

    allowed = {"volume", "image"}
    extras = sorted(str(key) for key in args if key not in allowed)
    if extras:
        raise ToolError(
            "bad_argument",
            "unsupported model_cache_inventory argument(s)",
            {"arguments": extras},
        )
    volume = _str_arg(args, "volume", models.DEFAULT_PULL_VOLUME)
    image = _str_arg(args, "image", models.DEFAULT_PULL_IMAGE)
    try:
        inventory = models.cache_inventory(volume=volume, image=image)
    except ValueError as exc:
        raise ToolError(
            "model_cache_inventory_failed",
            str(exc),
            {"volume": volume, "image": image},
        ) from exc
    return _ok({"inventory": inventory})


def tool_recipe_containers(args: dict) -> dict:
    """Return bounded recipe-container identities without registry dependence."""
    from .... import serve_recipes

    allowed = {"model", "container"}
    extras = sorted(str(key) for key in args if key not in allowed)
    if extras:
        raise ToolError(
            "bad_argument",
            "unsupported recipe_containers argument(s)",
            {"arguments": extras},
        )
    model = _str_arg(args, "model", "")
    container = _str_arg(args, "container", "")
    try:
        inventory = serve_recipes.discover_recipe_containers()
        if model or container:
            selected = serve_recipes.select_recipe_container(
                inventory,
                model=model or None,
                container=container or None,
            )
            inventory = {
                "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
                "containers": [selected],
            }
    except serve_recipes.RecipeError as exc:
        raise ToolError("recipe_container_discovery_failed", str(exc)) from exc
    return _ok({"inventory": inventory})


_RECIPE_SETTING_FLAGS = {
    "maximum_context": (("--max-model-len", "--context-length"), 256, 1048576),
    "concurrent_sequences": (("--max-num-seqs", "--max-running-requests"), 1, 4096),
}


def _recipe_settings(recipe: dict) -> dict:
    serve = recipe.get("serve") or {}
    result = {"startup_timeout_seconds": serve.get("startup_timeout_seconds")}
    flags = serve.get("flags") or []
    for setting, (aliases, _low, _high) in _RECIPE_SETTING_FLAGS.items():
        matches = [item for item in flags if isinstance(item, str) and item.split(" ", 1)[0] in aliases]
        if len(matches) == 1:
            try:
                result[setting] = int(matches[0].split(None, 1)[1])
            except (IndexError, ValueError):
                result[setting] = None
        else:
            result[setting] = None
    return result


def _tool_recipe_settings(args: dict, *, lock_held: bool = False) -> dict:
    """Read or atomically update three closed managed-recipe settings."""
    from .... import serve_recipes

    action = _str_arg(args, "action", required=True)
    if action not in {"status", "preview", "apply"}:
        raise ToolError("bad_action", "action must be one of: status, preview, apply")
    registry_path = os.path.abspath(os.path.expanduser(_str_arg(args, "registry", required=True)))
    model = _str_arg(args, "model", required=True)
    expected = _str_arg(args, "expected_baseline_sha256", "")
    values = args.get("values", {})
    if not isinstance(values, dict) or set(values) - ({"startup_timeout_seconds"} | set(_RECIPE_SETTING_FLAGS)):
        raise ToolError("bad_argument", "values contains an unsupported recipe setting")
    try:
        registry = serve_recipes.load_registry(registry_path)
        recipe = serve_recipes.find_recipe(registry, model)
        if recipe is None:
            raise serve_recipes.RecipeError("no serve recipe for %r" % model)
    except (OSError, serve_recipes.RecipeError) as exc:
        raise ToolError("recipe_unavailable", str(exc)) from exc
    baseline = serve_recipes.registry_digest(registry_path)
    if expected and expected != baseline:
        raise ToolError("recipe_conflict", "recipe registry changed after preview")
    before = _recipe_settings(recipe)
    replacement = copy.deepcopy(recipe)
    serve = replacement.setdefault("serve", {})
    flags = list(serve.get("flags") or [])
    for key, value in values.items():
        if key == "startup_timeout_seconds":
            low, high = 1, 14400
            if type(value) is not int or value < low or value > high:
                raise ToolError("bad_argument", "startup_timeout_seconds is outside its supported range")
            serve[key] = value
            continue
        aliases, low, high = _RECIPE_SETTING_FLAGS[key]
        if type(value) is not int or value < low or value > high:
            raise ToolError("bad_argument", "%s is outside its supported range" % key)
        positions = [i for i, item in enumerate(flags) if isinstance(item, str) and item.split(" ", 1)[0] in aliases]
        if len(positions) != 1:
            raise ToolError("unsupported_recipe", "%s is not uniquely declared in this recipe" % key)
        flag = flags[positions[0]].split(" ", 1)[0]
        flags[positions[0]] = "%s %d" % (flag, value)
    serve["flags"] = flags
    try:
        candidate_registry, _ = serve_recipes.update_recipe(registry, model, replacement)
        candidate_text = serve_recipes.format_registry(candidate_registry)
        candidate_digest = __import__("hashlib").sha256(candidate_text.encode()).hexdigest()
    except serve_recipes.RecipeError as exc:
        raise ToolError("bad_candidate", str(exc)) from exc
    if action in {"status", "preview"}:
        return _ok({"applied": False, "dry_run": True, "model": model,
                    "configured": before, "candidate": _recipe_settings(replacement),
                    "baseline_sha256": baseline, "candidate_sha256": candidate_digest})
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    human = _arg_bool(args.get("human_approved"), False, name="human_approved")
    if dry_run or not confirm or not human:
        raise ToolError("human_approval_required", "recipe apply requires the confirmed human gate")
    try:
        with open(registry_path, "rb") as handle:
            original = handle.read()
        if not lock_held:
            raise ToolError("recipe_lock_required", "recipe apply requires its owner lock")
        if serve_recipes.registry_digest(registry_path) != baseline:
            raise ToolError("recipe_conflict", "recipe registry changed during apply")
        backup = registry_path + ".observatory-backup-" + baseline[:16]
        try:
            with open(backup, "xb") as handle:
                handle.write(original)
        except FileExistsError:
            pass
        import tempfile
        fd, temporary = tempfile.mkstemp(
            prefix=".serve-recipes-", suffix=".toml", dir=os.path.dirname(registry_path) or "."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(candidate_text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, registry_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except ToolError:
        raise
    except (OSError, serve_recipes.RecipeError) as exc:
        raise ToolError("recipe_apply_failed", str(exc)) from exc
    return _ok({"applied": True, "dry_run": False, "model": model,
                "configured": _recipe_settings(replacement), "baseline_sha256": baseline,
                "candidate_sha256": candidate_digest,
                "recovery": {"status": "available", "backup_id": baseline[:16]}})


def tool_recipe_settings(args: dict) -> dict:
    from .... import serve_recipes

    registry = os.path.abspath(os.path.expanduser(_str_arg(args, "registry", required=True)))
    with serve_recipes.registry_lock(registry):
        return _tool_recipe_settings(args, lock_held=args.get("action") == "apply")


def _cache_prune_plan_argv(mixture: list[str], *, include_servable: bool) -> list[str]:
    argv = [sys.executable, "-m", "anvil_serving.cli", "models", "cache", "prune", "--json"]
    if mixture:
        argv += ["--mixture", ",".join(mixture)]
    if include_servable:
        argv.append("--include-servable")
    return argv


def tool_cache_prune_plan(args: dict) -> dict:
    from .... import cache_prune

    allowed = {"mixture", "include_servable", "execute", "confirm", "yes", "dry_run"}
    extras = sorted(str(key) for key in args if key not in allowed)
    if extras:
        raise ToolError(
            "bad_argument", "unsupported cache_prune_plan argument(s)", {"arguments": extras}
        )
    for name in ("execute", "confirm", "yes"):
        if _arg_bool(args.get(name), False, name=name):
            raise ToolError(
                "cache_prune_delete_not_available",
                "cache_prune_plan is read-only; destructive pruning requires the human-gated CLI",
                {"requested": name},
            )
    if args.get("dry_run") is not None and not _arg_bool(args.get("dry_run"), True, name="dry_run"):
        raise ToolError(
            "cache_prune_delete_not_available",
            "cache_prune_plan cannot disable dry_run through MCP",
            {"requested": "dry_run=false"},
        )

    mixture = sorted(set(_str_list_arg(args, "mixture")))
    include_servable = _arg_bool(args.get("include_servable"), False, name="include_servable")
    argv = _cache_prune_plan_argv(mixture, include_servable=include_servable)
    try:
        plan = cache_prune.build_plan(set(mixture))
        report = cache_prune.execute_plan(plan, dry_run=True, include_servable=include_servable)
    except Exception as exc:
        raise ToolError("cache_prune_plan_failed", str(exc), {"command": argv})
    return _ok(
        {
            "dry_run": True,
            "deletion_available": False,
            "human_gate_required": True,
            "command": argv,
            "mixture": mixture,
            "include_servable": include_servable,
            "plan": plan,
            "report": report,
        }
    )


FAMILY = ToolFamily(
    name="models",
    tools={
        "models_inventory": {
            "description": "Read the generated model catalog, or preview/run `models sync` to create it.",
            "inputSchema": _schema(
                {
                    "catalog_dir": {"type": "string"},
                    "hf_roots": {"type": "string"},
                    "model_dirs": {"type": "string"},
                    "sync": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 1800),
                }
            ),
            "handler": tool_models_inventory,
        },
        "model_cache_inventory": {
            "description": (
                "Read one Docker model-cache volume plus Docker image, volume, "
                "container, and build-cache accounting."
            ),
            "inputSchema": _schema(
                {
                    "volume": {"type": "string"},
                    "image": {"type": "string"},
                }
            ),
            "handler": tool_model_cache_inventory,
        },
        "recipe_containers": {
            "description": (
                "Discover bounded, label-owned Anvil recipe containers without "
                "reading registry state, environment values, or raw commands."
            ),
            "inputSchema": _schema(
                {
                    "model": {"type": "string"},
                    "container": {"type": "string"},
                }
            ),
            "handler": tool_recipe_containers,
        },
        "recipe_settings": {
            "description": "Read, preview, or atomically apply bounded managed-recipe runtime settings.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string", "enum": ["status", "preview", "apply"]},
                    "registry": {"type": "string"}, "model": {"type": "string"},
                    "values": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "maximum_context": _bounded_integer_schema(256, 1048576, 256),
                                   "concurrent_sequences": _bounded_integer_schema(1, 4096, 1),
                                   "startup_timeout_seconds": _bounded_integer_schema(1, 14400, 1),
                               }},
                    "expected_baseline_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    "dry_run": {"type": "boolean"}, "confirm": {"type": "boolean"},
                    "human_approved": {"type": "boolean"},
                }, required=["action", "registry", "model"],
            ),
            "handler": tool_recipe_settings,
        },
        "cache_prune_plan": {
            "description": "Return a JSON model-cache prune plan and dry-run report; deletion is not available through MCP.",
            "inputSchema": _schema(
                {
                    "mixture": {"type": "array", "items": {"type": "string"}},
                    "include_servable": {"type": "boolean"},
                }
            ),
            "handler": tool_cache_prune_plan,
        },
    },
)
