"""anvil-serving harness — own the HARNESS-side config, not just the router.

`serves` manages the model backends and `router` manages the deployed front door; this verb
extends that ownership to the harness anvil fronts (CLAUDE.md golden rule "anvil-serving owns the
harness-side config too"). It RENDERS the harness's config FROM the live router config so the two
never drift — instead of hand-editing the gateway out-of-band.

v1 target: **OpenClaw**. `harness sync openclaw --config <router.toml>` emits the OpenClaw provider
config — one selectable model per router preset, each `contextWindow` set to the LARGEST tier that
preset can route to (the contextWindow-clamp gotcha, docs/OPENCLAW-INTEGRATION-SPEC.md §2). It
does NOT emit per-preset thinking overrides: the router owns reasoning/thinking per tier now
(heavy `reasoning_effort`, fast `enable_thinking`), so re-declaring them on the harness is stale.

Skills / agent-config sync is the next step (`--skills`, not yet implemented) — the harness's
`before_model_resolve` plugin + skills are the follow-on scope.

The OpenClaw GATEWAY is typically REMOTE from the router (e.g. Fakoli Mini -> fakoli-dark), so v1
EMITS the config (stdout or `--out`) for the operator to place at `~/.openclaw/openclaw.json` on the
gateway; a future `--gateway-host` can apply it over ssh. stdlib-only.
"""
import argparse
import json
import sys

# Per-preset OpenClaw hints (advisory display/caps; contextWindow is computed from the router).
# maxTokens is the harness's output cap; input declares modalities (review accepts images).
_PRESET_MAX_TOKENS = {
    "planning": 32000, "review": 16000, "long-context": 16000,
    "quick-edit": 8192, "chat": 8192,
}
_PRESET_INPUT = {"review": ["text", "image"]}
_DEFAULT_MAX_TOKENS = 8192


def _title(preset_id):
    """`quick-edit` -> `Quick Edit` for the OpenClaw display name."""
    return " ".join(w.capitalize() for w in preset_id.replace("_", "-").split("-"))


def render_openclaw_provider(config, *, base_url, api_key_env="ANVIL_ROUTER_TOKEN"):
    """Render the OpenClaw provider + agent config dict from a loaded RouterConfig.

    One model per preset; `contextWindow` = max `context_limit` among the preset's candidate
    tiers (so a request within the window always fits SOME routed tier — clamp gotcha). No
    per-preset thinking overrides (the router owns reasoning/thinking per tier).
    """
    models = []
    for preset_id, tier_ids in config.presets.items():
        windows = [config.tier(t).context_limit for t in tier_ids]
        ctx = max(windows) if windows else 0
        models.append({
            "id": preset_id,
            "name": "Anvil · " + _title(preset_id),
            "reasoning": False,
            "input": _PRESET_INPUT.get(preset_id, ["text"]),
            "contextWindow": ctx,
            "maxTokens": _PRESET_MAX_TOKENS.get(preset_id, _DEFAULT_MAX_TOKENS),
        })
    return {
        "models": {
            "mode": "merge",
            "providers": {
                "anvil": {
                    "baseUrl": base_url,
                    "apiKey": "${%s}" % api_key_env,
                    "api": "openai-completions",
                    "models": models,
                }
            },
        },
        # Default slot only. NO per-preset thinking overrides — the router owns reasoning/thinking
        # per tier (heavy reasoning_effort / fast enable_thinking); declaring them here would drift.
        "agents": {"defaults": {"model": {"primary": "anvil/chat"}, "models": {}}},
        "plugins": {"entries": {"anvil-intent-router": {"hooks": {"allowConversationAccess": True}}}},
    }


def cmd_sync_openclaw(config_path, *, out=None, base_url, api_key_env, skills=False,
                      _load=None):
    if skills:
        print("harness sync openclaw --skills: skills/agent-config sync is not implemented yet "
              "(v1 syncs models only); tracking as the next scope.", file=sys.stderr)
        return 2
    if _load is None:
        from .router.config import load as _load
    try:
        config = _load(config_path)
    except FileNotFoundError:
        print("router config not found: %s" % config_path, file=sys.stderr)
        return 2
    except Exception as e:  # malformed config
        print("cannot load router config %s: %s" % (config_path, e), file=sys.stderr)
        return 2
    if not getattr(config, "presets", None):
        print("router config %s declares no [router.presets] — nothing to sync." % config_path,
              file=sys.stderr)
        return 1
    provider = render_openclaw_provider(config, base_url=base_url, api_key_env=api_key_env)
    text = json.dumps(provider, indent=2, ensure_ascii=False) + "\n"
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        n = len(provider["models"]["providers"]["anvil"]["models"])
        print("wrote OpenClaw provider config (%d preset models) -> %s" % (n, out))
        print("  apply on the OpenClaw gateway at ~/.openclaw/openclaw.json (merge mode); the "
              "gateway may be REMOTE from the router (set --base-url to the router's reachable host).")
    else:
        sys.stdout.write(text)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="anvil-serving harness",
        description="Own the harness-side config: render a harness's model/provider config FROM "
                    "the live router config so the two never drift. v1: OpenClaw models.")
    p.add_argument("action", choices=["sync"],
                   help="sync: render the harness config from the router config.")
    p.add_argument("harness", choices=["openclaw"], help="target harness (v1: openclaw).")
    p.add_argument("--config", required=True,
                   help="router config TOML to render the harness models from (its presets + tier "
                        "context limits).")
    p.add_argument("--out", help="write the harness config here (default: stdout).")
    p.add_argument("--base-url", default="http://127.0.0.1:8000/v1",
                   help="the router front door the harness dials (default: %(default)s; use the "
                        "router's reachable host when the gateway is REMOTE).")
    p.add_argument("--api-key-env", default="ANVIL_ROUTER_TOKEN",
                   help="env var name holding the router bearer token (default: %(default)s); the "
                        "emitted config references it by name, never the secret.")
    p.add_argument("--skills", action="store_true",
                   help="(not yet implemented) also sync skills/agent config.")
    a = p.parse_args(argv)

    if a.action == "sync" and a.harness == "openclaw":
        return cmd_sync_openclaw(a.config, out=a.out, base_url=a.base_url,
                                 api_key_env=a.api_key_env, skills=a.skills)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
