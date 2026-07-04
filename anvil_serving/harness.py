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

The OpenClaw GATEWAY is typically REMOTE from the router (e.g. Fakoli Mini -> fakoli-dark), so this
either EMITS the config (stdout or `--out`) OR pushes it to the remote gateway over ssh with
`--gateway-host` — MERGING the anvil provider into the remote `~/.openclaw/openclaw.json` (preserving
the operator's other providers/agents/plugins) and backing up the remote first; `--overwrite` does a
full write. stdlib-only (ssh via `subprocess`, injected for tests).
"""
import argparse
import json
import subprocess
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


# --------------------------------------------------------------------------- #
# remote (ssh) sync — the OpenClaw gateway is typically REMOTE from the router
# --------------------------------------------------------------------------- #

def _ssh_target(host, user):
    return ("%s@%s" % (user, host)) if user else host


def _merge_anvil_provider(existing, rendered):
    """Merge ONLY anvil-owned keys of `rendered` into the operator's existing OpenClaw config,
    preserving their OTHER providers / agents / plugins. Returns a NEW dict (existing untouched)."""
    out = json.loads(json.dumps(existing))  # deep copy
    models = out.setdefault("models", {})
    models["mode"] = "merge"
    models.setdefault("providers", {})["anvil"] = rendered["models"]["providers"]["anvil"]
    defaults = out.setdefault("agents", {}).setdefault("defaults", {})
    defaults.setdefault("model", {}).setdefault("primary", "anvil/chat")
    # drop any stale per-preset overrides for anvil/* — the router owns reasoning/thinking now.
    dmodels = defaults.get("models")
    if isinstance(dmodels, dict):
        for k in [k for k in dmodels if str(k).startswith("anvil/")]:
            del dmodels[k]
    out.setdefault("plugins", {}).setdefault("entries", {})["anvil-intent-router"] = \
        rendered["plugins"]["entries"]["anvil-intent-router"]
    return out


def _sync_over_ssh(host, user, path, rendered, *, overwrite, _run):
    """Push the rendered config to a REMOTE gateway over ssh, backing up the remote file first.

    Default MERGES the anvil provider into the existing remote config (preserving the operator's
    other settings); `overwrite` writes the full rendered config. A merge is REFUSED if the remote
    file isn't plain JSON (JSON5/comments) — re-run with --overwrite (a timestamped .bak is always
    taken first). Returns 0 on success, 1 on failure. All ssh goes through the injected `_run`."""
    tgt = _ssh_target(host, user)
    try:
        r = _run(["ssh", tgt, "cat %s 2>/dev/null || true" % path],
                 capture_output=True, text=True)
    except FileNotFoundError:
        print("ssh not available on PATH", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print("cannot reach %s over ssh: %s" % (tgt, (r.stderr or "").strip()), file=sys.stderr)
        return 1
    existing_text = r.stdout or ""

    if overwrite or not existing_text.strip():
        payload, mode = rendered, ("overwrite" if existing_text.strip() else "created")
    else:
        try:
            payload = _merge_anvil_provider(json.loads(existing_text), rendered)
            mode = "merged"
        except ValueError:
            print("refusing to merge: remote %s is not plain JSON (JSON5/comments?). Re-run with "
                  "--overwrite (a .bak is taken first), or edit it by hand." % path, file=sys.stderr)
            return 1

    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    # backup (if present) then ATOMICALLY write via temp + mv on the remote.
    script = ("[ -f %(p)s ] && cp %(p)s %(p)s.bak.$(date +%%s) || true; "
              "cat > %(p)s.anvil-new && mv %(p)s.anvil-new %(p)s") % {"p": path}
    w = _run(["ssh", tgt, script], input=text, capture_output=True, text=True)
    if w.returncode != 0:
        print("FAILED to write %s on %s: %s"
              % (path, tgt, (w.stderr or w.stdout or "").strip()), file=sys.stderr)
        return 1
    n = len(rendered["models"]["providers"]["anvil"]["models"])
    print("synced OpenClaw provider (%d preset models, %s) -> %s:%s (backup taken)"
          % (n, mode, tgt, path))
    return 0


def cmd_sync_openclaw(config_path, *, out=None, base_url, api_key_env, skills=False,
                      gateway_host=None, gateway_user=None,
                      gateway_path="~/.openclaw/openclaw.json", overwrite=False,
                      _load=None, _run=subprocess.run):
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

    if gateway_host:  # push to the REMOTE gateway over ssh (Mini -> the router)
        return _sync_over_ssh(gateway_host, gateway_user, gateway_path, provider,
                              overwrite=overwrite, _run=_run)

    text = json.dumps(provider, indent=2, ensure_ascii=False) + "\n"
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        n = len(provider["models"]["providers"]["anvil"]["models"])
        print("wrote OpenClaw provider config (%d preset models) -> %s" % (n, out))
        print("  apply on the OpenClaw gateway at ~/.openclaw/openclaw.json, or push directly with "
              "--gateway-host <mini> (ssh; merges by default, backs up the remote first).")
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
    p.add_argument("--gateway-host",
                   help="push the config to a REMOTE OpenClaw gateway over ssh (e.g. fakoli-mini); "
                        "MERGES the anvil provider into the remote config by default (backup taken).")
    p.add_argument("--gateway-user", help="ssh user for --gateway-host (default: your ssh config).")
    p.add_argument("--gateway-path", default="~/.openclaw/openclaw.json",
                   help="remote OpenClaw config path for --gateway-host (default: %(default)s).")
    p.add_argument("--overwrite", action="store_true",
                   help="with --gateway-host: OVERWRITE the remote config instead of merging "
                        "(a timestamped .bak is taken first either way).")
    p.add_argument("--skills", action="store_true",
                   help="(not yet implemented) also sync skills/agent config.")
    a = p.parse_args(argv)

    if a.action == "sync" and a.harness == "openclaw":
        return cmd_sync_openclaw(a.config, out=a.out, base_url=a.base_url,
                                 api_key_env=a.api_key_env, skills=a.skills,
                                 gateway_host=a.gateway_host, gateway_user=a.gateway_user,
                                 gateway_path=a.gateway_path, overwrite=a.overwrite)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
