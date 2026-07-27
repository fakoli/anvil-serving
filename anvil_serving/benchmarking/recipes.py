"""Serve-recipe construction and emission from benchmark results."""

import sys


def serve_recipes():
    """Import the shared recipe helpers without introducing an import cycle."""
    from .. import serve_recipes as module

    return module


def build_recipe(args, summary, *, capture=None, hardware=None):
    """Assemble a serve recipe from one completed benchmark run."""
    recipes = serve_recipes()
    capture = capture or recipes.capture_from_container
    hardware = hardware or recipes.capture_hardware

    captured = capture(args.recipe_from_container) if args.recipe_from_container else {}
    serve = dict(captured.get("serve") or {})
    captured_hardware = dict(captured.get("hardware") or {})
    gpu_uuid = captured_hardware.get("gpu_uuid")
    gpu = hardware(gpu_uuid) if gpu_uuid else {}

    recipe = {"model": args.recipe_model or args.model, "status": args.recipe_status}
    recipe["source"] = (
        "measured via anvil-serving eval benchmark run (%s)"
        % summary.get("run_id", "")
    )

    hardware_block = {}
    if gpu.get("gpu"):
        hardware_block["gpu"] = gpu["gpu"]
    if gpu.get("vram_total_gb") is not None:
        hardware_block["vram_total_gb"] = gpu["vram_total_gb"]
    if gpu_uuid:
        hardware_block["gpu_uuid"] = gpu_uuid
    if hardware_block:
        recipe["hardware"] = hardware_block

    context_tokens = summary.get("context_tokens") or summary.get("max_context_tokens")
    if context_tokens and "context_tokens" not in serve:
        serve["context_tokens"] = context_tokens
    if serve:
        recipe["serve"] = serve

    metrics = summary.get("metrics") or {}
    measured = {}
    throughput = metrics.get("throughput_tok_s")
    if throughput is not None:
        concurrency = summary.get("concurrency") or 1
        if concurrency == 1:
            measured["throughput_single_tok_s"] = round(throughput, 1)
        else:
            measured["throughput_aggregate_tok_s"] = round(throughput, 1)
            measured["concurrency"] = concurrency
    ttft = metrics.get("ttft_p50_ms")
    if ttft is not None:
        measured["ttft_p50_ms"] = round(ttft, 1)
    if context_tokens:
        measured["context_tokens"] = context_tokens
    if measured:
        recipe["measured"] = measured

    if args.recipe_fit:
        suited = [item.strip() for item in args.recipe_fit.split(",") if item.strip()]
        if suited:
            recipe["fit"] = {"suited": suited}

    return recipe


def emit_recipe(args, summary, *, capture=None, hardware=None, append=None):
    """Render and persist a recipe, or emit it to stdout."""
    recipes = serve_recipes()
    recipe = build_recipe(args, summary, capture=capture, hardware=hardware)
    if args.recipe_out == "-":
        print(recipes.format_recipe(recipe), end="")
    else:
        try:
            (append or recipes.append_recipe)(args.recipe_out, recipe)
        except OSError as exc:
            print(
                "could not write serve recipe to %s: %s" % (args.recipe_out, exc),
                file=sys.stderr,
            )
            return recipe
        print("recorded serve recipe for %s -> %s" % (recipe["model"], args.recipe_out))
    return recipe
