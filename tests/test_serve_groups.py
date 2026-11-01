"""Tests for serve groups — `serves up/down/status --group` and `serves groups`.

Group resolution spans the whole manifest SET (every serves*.toml in the
manifest's directory), de-duped by container, and every member still runs
through the same ADR-0017 reservation admission path. Docker/nvidia-smi/HTTP are
injected via the module's `_run`/`_open` seams, so these run with no docker, no
GPU, and no network.
"""
import os
import textwrap

from anvil_serving import serves
from tests.conftest import proc


def _write(path, body):
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _set(tmp_path):
    """A two-file manifest set: an LLM manifest with a ledger + groups, and a
    voice manifest tagging stt/tts into the `voice` group.

    Budget is 10000 MiB: the `embedding` group (emb 3000 + rer 3000) fits, but
    the `stack` group (adds fast 6000 = 12000) is over budget — so admission is
    observable.
    """
    _write(tmp_path / "serves.toml", """
        [[gpu_roles]]
        id = "g"
        vram_mib = 10000
        reserve_mib = 0

        [[serve]]
        name = "fast"
        container = "c-fast"
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
        engine = "vllm"
        gpu_role = "g"
        vram_mib = 6000
        residency = "resident"
        groups = ["fast-only", "stack"]
        up = "docker compose up -d fast"

        [[serve]]
        name = "emb"
        container = "c-emb"
        runtime = "docker"
        port = 30005
        model = "emb-local"
        engine = "embedding"
        gpu_role = "g"
        vram_mib = 3000
        residency = "resident"
        groups = ["embedding", "stack"]
        up = "docker compose up -d emb"

        [[serve]]
        name = "rer"
        container = "c-rer"
        runtime = "docker"
        port = 30006
        model = "rer-local"
        engine = "reranker"
        gpu_role = "g"
        vram_mib = 3000
        residency = "resident"
        groups = ["embedding", "stack"]
        up = "docker compose up -d rer"

        [[serve]]
        name = "heavy"
        container = "c-heavy"
        runtime = "docker"
        port = 30002
        model = "primary-local"
        engine = "vllm"
        groups = ["heavy-only", "stack"]
        up = "docker compose up -d heavy"
    """)
    _write(tmp_path / "serves.voice.toml", """
        [[serve]]
        name = "stt"
        container = "c-stt"
        runtime = "docker"
        port = 30010
        model = "stt-local"
        engine = "audio"
        groups = ["voice"]
        up = "docker compose up -d stt"

        [[serve]]
        name = "tts"
        container = "c-tts"
        runtime = "docker"
        port = 30011
        model = "tts-local"
        engine = "audio"
        groups = ["voice"]
        up = "docker compose up -d tts"
    """)
    return str(tmp_path / "serves.toml")


# ---- manifest-set resolution ------------------------------------------------


# ---- group resolution -------------------------------------------------------


# ---- groups_summary / cmd_groups -------------------------------------------


# ---- CLI wiring through serves.main ----------------------------------------

def test_main_groups_lists_across_set(tmp_path, capsys):
    manifest = _set(tmp_path)
    assert serves.main(["groups", "--manifest", manifest]) == 0
    out = capsys.readouterr().out
    assert "voice" in out and "stt, tts" in out
    assert "embedding" in out


# ---- admission still enforced per group ------------------------------------

def _absent_run():
    def run(argv, **k):
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "Error: No such object")
        return proc(0, "", "")
    return run


def test_group_up_dry_run_starts_nothing(tmp_path):
    s = serves.load_manifest_set(_set(tmp_path))
    targets, _ = serves.resolve_group_targets(s, ["embedding"], [])
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "Error: No such object")
        return proc(0, "", "")

    serves.cmd_up(s, targets, dry_run=True, _run=run)
    # Only inventory/inspect probes ran; no compose/start/stop mutation.
    assert all(
        c[:2] == ["docker", "inspect"] or c[:3] == ["docker", "ps", "-a"]
        for c in calls if isinstance(c, list)
    )


# ---- status --group keeps the whole-set ledger -----------------------------


# ---- shipped manifests carry the authored groups ---------------------------

EXAMPLE_DIR = os.path.dirname(serves.EXAMPLE_MANIFEST)


def test_shipped_example_manifests_authored_groups():
    s = serves.load_manifest_set(serves.EXAMPLE_MANIFEST)
    by_name = {x["name"]: set(x.get("groups") or []) for x in s}
    assert by_name["primary"] == {"primary-only", "llm-stack"}
    assert by_name["omni"] == {"omni-stack", "llm-stack"}
    assert by_name["omni-small"] == {"omni-small-stack", "omni-voice-stack"}
    assert by_name["embeddings"] == {"embedding", "auxiliary-stack"}
    assert by_name["reranker"] == {"embedding", "auxiliary-stack"}
    assert by_name["stt"] == {"voice", "omni-voice-stack"}
    assert by_name["tts"] == {"voice", "omni-voice-stack"}
    assert by_name["media-worker"] == {"comfy", "media"}
