# Handoff — dedicated `voice up` / `voice down` for the realtime voice proxy

**For:** a fresh Claude Code session continuing this branch (`agent/voice-lifecycle-up-down`, cut from `main` @ `5687683`, which already has #295 + #296).
**Status:** design done, not started. This doc is the spec. Delete it before opening the PR.
**Prepared by:** the Workbench session that shipped #295 (the managed-container proxy) + #296 (the ruff pin).

---

## 1. Goal & acceptance

Add first-class **`anvil-serving voice up`** / **`anvil-serving voice down`** verbs that bring the whole voice stack — the **STT + TTS audio serves** *and* the **realtime proxy** — up (and down) in one command.

Acceptance:
- `voice up` brings up STT, TTS, then the proxy — **in that order** (the proxy preflights STT/TTS/router reachability at start, so it must come up **last**).
- `voice down` tears down in **reverse**: proxy first, then the audio serves.
- Both honor the existing `--dry-run` / `--confirm` mutation semantics and return a combined result **dict** (not prints — see `AGENTS.md`).
- The v2 command tree, the CLI-reference audit, and the regenerated manifest all agree (the audit gate fails closed otherwise).
- Full suite green: `python -m pytest tests/ -x -q`; lint green: `ruff check .` (ruff is now pinned to `0.15.20` in CI — match that locally: `pip install ruff==0.15.20`).

## 2. Why this exists (context)

The realtime voice proxy is now a **harness-independent managed service** (shipped in #295): it runs as a Docker container reusing the anvil-serving image (`voice proxy run`), configured with **direct** `stt_url`/`tts_url`/`router_url` (no per-harness topology), so Workbench, OpenClaw, etc. just point a relay at its WS endpoint. Operators asked for a single `voice up`/`voice down` to manage the whole voice stack instead of running `voice audio up` + `voice proxy up` separately.

## 3. What `main` already has (the pieces you orchestrate — do NOT reinvent)

All on `main` @ `5687683`:
- **Audio lifecycle:** `voice audio up|down|status|logs` → `execute_audio_lifecycle(...)` in `anvil_serving/voice/cli.py` (~L256), which fans over `_audio_serves` (stt, tts) and dispatches per-serve by `lifecycle` (managed→`ServeLifecycle`, native, external).
- **Proxy lifecycle:** `voice proxy up|down|restart|status|logs` → `cmd_proxy_lifecycle(...)` (`voice/cli.py` ~L845), which branches on `[voice.proxy].lifecycle`: `managed`→`ProxyServe` (Docker container), `native`→`RealtimeProxyProcessService` (detached subprocess). #295 added `anvil_serving/voice/serves/proxy.py` and the `[voice.proxy]` direct-config table.
- **Existing aggregate for the co-located case:** `serves up --group voice` already brings up **stt + tts + realtime-proxy** together, because all three carry `groups = ["voice"]` in `serves.voice.toml`. `voice up`/`voice down` is the first-class *verb* over the same idea (and covers the split-host case per the scoping decision in §6).

**The new `voice up`/`voice down` is a thin fan-out over `execute_audio_lifecycle` + `cmd_proxy_lifecycle`.** Reuse them; do not duplicate their per-serve logic.

## 4. Do NOT build on the T019 branch

`agent/operator-cli-v2-t019-define-the-voice-proxy-lifecycle-service` is **stale and superseded** — it branched from an older main (before #295), its `command_tree.py` is ~546 lines vs main's ~2000, and it **does not** add `voice up`/`voice down` (it tombstones them too). Its voice work is an *in-process thread* `RealtimeProxyService` — a competing model to #295's shipped container/subprocess path. Cherry-picking it would conflict and regress. Its **only** reference value is the bounded-shutdown discipline (`RealtimeProxyStopTimeoutError`, idempotent stop) if you later harden the run loop. Ignore it otherwise.

## 5. Exact files to change

1. **`anvil_serving/voice/cli.py`**
   - Add `cmd_lifecycle_up` / `cmd_lifecycle_down` that fan out: **up** = `execute_audio_lifecycle(up)` → then `cmd_proxy_lifecycle(up)`; **down** = proxy down → then audio down. Combine into one result dict; if audio-up fails, do **not** start the proxy (and say so).
   - Add the `up` / `down` subparsers under the `voice` group.
   - Remove `up`/`down` from the **runtime hard-block** `removed_audio_paths` (~`voice/cli.py:1648-1663`, returns exit 2). **Keep `start`/`stop`/`run`/`bridge` blocked** — only free `up`/`down`. This is a *second, independent* enforcement layer from the tombstone; both must be lifted.
2. **`anvil_serving/command_tree.py`**
   - Delete the `("up","voice audio up")` / `("down","voice audio down")` entries from the tombstone generator (~L1968-1971).
   - Add real `_resource_node("up", …)` / `_resource_node("down", …)` children under the `voice` node (~L1919-1975) pointing at the new handlers, with `mutation="mutate"`, an `argv_prefix`, review-metadata/examples, and role wiring per §6.
3. **`scripts/audit_cli_references.py`**
   - Move `voice-up-down` out of `LEGACY_PATTERNS` (~L53) **and** its `_BARE_LEGACY_RE` entry (~L92-104); add a `CANONICAL_PATTERNS` entry, e.g. `"voice-lifecycle": r"voice\s+(?:up|down)\b"`.
4. **Regenerate + docs** — run `python scripts/audit_cli_references.py --scope full --update` to regenerate `docs/CLI-COMMAND-MANIFEST.json` and the tombstone/migration index blocks; then update `docs/cli/voice.md` / `docs/VOICE.md` and the `docs/CLI.md` migration section to document `voice up`/`voice down`.
5. **Tests** — `tests/voice/test_voice_cli.py` (aggregate up/down + ordering + dry-run + audio-up-fails-skips-proxy), `tests/test_command_tree.py` (the new nodes exist / are not tombstoned), and the audit fixture under `tests/fixtures/cli_reference_audit`.

## 6. The load-bearing decision — two owners, one node

**Biggest risk / real modeling tension:** an aggregate `voice up` spans **two different owners/transports** — the **Dark-owned** STT/TTS serves (`resolve_audio_targets`) and the **Mini-owned** realtime proxy (`resolve_proxy_targets`), resolved and gated *separately* with different owner hosts and refusal rules. The v2 `CommandNode` binds one node to a **single** owner resolution + `remote_operation`. A genuine two-owner node breaks that one-node-one-owner assumption the tree exists to enforce.

**Recommended scope (do this):** scope `voice up`/`voice down` to the **single-host / managed case** — where `[voice.proxy]` is direct-configured and all three serves are co-located (i.e. the `serves --group voice` world #295 shipped). Model the node with `role`/`coowned_roles` for that co-located ownership. **Keep the cross-host Mini→Dark split on the explicit `voice audio …` + `voice proxy …` commands.** Document this boundary in the help text. Going for a true two-owner `voice up` reintroduces exactly the cross-owner privilege ambiguity the v2 tree was built to prevent — only do that if you deliberately extend the node model, which is a much bigger change.

`CommandNode` precedent to copy: `voice proxy up` uses `role="realtime-proxy", coowned_roles=("stt-proxy","tts-proxy")`; `voice audio up` uses `role="stt-serve", coowned_roles=("tts-serve",)` (~`command_tree.py:1923-1957`). One node *can* declare multiple co-owned roles, but it still maps to one handler + one owner resolution — hence the single-host scoping.

## 7. Verification checklist

- `ruff check .` clean (install `ruff==0.15.20` to match CI).
- `python scripts/audit_cli_references.py --scope full` passes (no `voice up`/`voice down` violation; the canonical pattern accepts them).
- `python -m pytest tests/ -x -q` green.
- Manual: `voice up --dry-run` shows audio-then-proxy; `voice down --dry-run` shows proxy-then-audio; both emit a combined dict.
- Delete this handoff file before opening the PR.

## 8. Gotchas

- The command tree is the **single source of truth**; dispatch is tree-driven (`cli.py` imports `COMMAND_TREE`). The node + manifest regen + audit allow-list must land **together** or the audit gate fails closed.
- Two enforcement layers block `up`/`down` today (the `command_tree.py` tombstone **and** the `removed_audio_paths` runtime block) — free **both**.
- Preserve `--confirm`/`--dry-run` on the `mutate` verbs.
- Order matters and is asymmetric: **up = audio→proxy, down = proxy→audio.**
