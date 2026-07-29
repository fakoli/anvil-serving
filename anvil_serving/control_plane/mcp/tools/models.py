"""Explicit models MCP tool family."""

from __future__ import annotations

import os
import sys

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
