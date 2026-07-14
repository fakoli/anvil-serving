"""Tests for serve groups — `serves up/down/status --group` and `serves groups`.

Group resolution spans the whole manifest SET (every serves*.toml in the
manifest's directory), de-duped by container, and every member still runs
through the same ADR-0017 reservation admission path. Docker/nvidia-smi/HTTP are
injected via the module's `_run`/`_open` seams, so these run with no docker, no
GPU, and no network.
"""
import json
import os
import textwrap
import types

from anvil_serving import reservations, serves


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


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
        port = 30001
        model = "fast-local"
        engine = "vllm"
        gpu_role = "g"
        vram_mib = 6000
        residency = "resident"
        groups = ["fast-only", "stack"]
        up = "docker compose up -d fast"

        [[serve]]
        name = "emb"
        container = "c-emb"
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
        port = 30002
        model = "heavy-local"
        engine = "vllm"
        groups = ["heavy-only", "stack"]
        up = "docker compose up -d heavy"
    """)
    _write(tmp_path / "serves.voice.toml", """
        [[serve]]
        name = "stt"
        container = "c-stt"
        port = 30010
        model = "stt-local"
        engine = "audio"
        groups = ["voice"]
        up = "docker compose up -d stt"

        [[serve]]
        name = "tts"
        container = "c-tts"
        port = 30011
        model = "tts-local"
        engine = "audio"
        groups = ["voice"]
        up = "docker compose up -d tts"
    """)
    return str(tmp_path / "serves.toml")


# ---- manifest-set resolution ------------------------------------------------

def test_manifest_set_spans_all_serves_toml(tmp_path):
    manifest = _set(tmp_path)
    paths = serves.manifest_set_paths(manifest)
    names = sorted(os.path.basename(p) for p in paths)
    assert names == ["serves.toml", "serves.voice.toml"]


def test_load_manifest_set_dedupes_by_container_preferring_up(tmp_path):
    manifest = _set(tmp_path)
    # A second file re-declares c-emb as a read-only ledger mirror (no `up`).
    _write(tmp_path / "serves.comfyui.toml", """
        [[serve]]
        name = "emb"
        container = "c-emb"
        port = 30005
        model = "emb-local"
        engine = "embedding"
    """)
    s = serves.load_manifest_set(manifest)
    containers = [x["container"] for x in s]
    assert len(containers) == len(set(containers))  # de-duped by container
    emb = [x for x in s if x["container"] == "c-emb"][0]
    assert emb.get("up")  # the lifecycle-owning entry wins over the mirror


def test_load_manifest_set_ledger_not_double_counted(tmp_path):
    """A mirrored reservation is counted once, so `free` is honest."""
    manifest = _set(tmp_path)
    _write(tmp_path / "serves.comfyui.toml", """
        [[gpu_roles]]
        id = "g"
        vram_mib = 10000
        reserve_mib = 0

        [[serve]]
        name = "fast"
        container = "c-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        gpu_role = "g"
        vram_mib = 6000
        residency = "resident"
    """)
    s = serves.load_manifest_set(manifest)
    ledger = reservations.build_ledger(s, lambda c: "running")
    role = ledger["g"]
    # fast+emb+rer committed once each = 12000, not 18000 from the mirror.
    assert role.committed_mib == 12000


# ---- group resolution -------------------------------------------------------

def test_resolve_group_spans_files(tmp_path):
    s = serves.load_manifest_set(_set(tmp_path))
    voice = serves.resolve_group(s, "voice")
    assert sorted(x["name"] for x in voice) == ["stt", "tts"]


def test_reserved_all_selects_every_serve(tmp_path):
    s = serves.load_manifest_set(_set(tmp_path))
    every = serves.resolve_group(s, "all")
    assert {x["name"] for x in every} == {"fast", "emb", "rer", "heavy", "stt", "tts"}


def test_select_groups_unions_and_reports_unknown(tmp_path):
    s = serves.load_manifest_set(_set(tmp_path))
    selected, unknown = serves.select_groups(s, ["embedding", "voice"])
    assert sorted(x["name"] for x in selected) == ["emb", "rer", "stt", "tts"]
    assert unknown == []
    _, unknown2 = serves.select_groups(s, ["nope"])
    assert unknown2 == ["nope"]


def test_resolve_group_targets_unions_group_with_names(tmp_path):
    s = serves.load_manifest_set(_set(tmp_path))
    targets, unknown = serves.resolve_group_targets(s, ["embedding"], ["heavy"])
    assert unknown == []
    assert sorted(targets) == ["emb", "heavy", "rer"]  # group ∪ positional name


def test_resolve_group_targets_dedupes_overlap(tmp_path):
    s = serves.load_manifest_set(_set(tmp_path))
    # `emb` is both in the embedding group and named positionally.
    targets, _ = serves.resolve_group_targets(s, ["embedding"], ["emb"])
    assert sorted(targets) == ["emb", "rer"]


# ---- groups_summary / cmd_groups -------------------------------------------

def test_groups_summary_lists_defined_groups(tmp_path):
    s = serves.load_manifest_set(_set(tmp_path))
    summary = serves.groups_summary(s)
    catalog = {row["group"]: row["serves"] for row in summary["groups"]}
    assert catalog["embedding"] == ["emb", "rer"]
    assert catalog["voice"] == ["stt", "tts"]
    assert catalog["stack"] == ["fast", "emb", "rer", "heavy"]
    assert set(summary["all"]) == {"fast", "emb", "rer", "heavy", "stt", "tts"}
    assert "all" not in catalog  # reserved, never an authored group row


def test_cmd_groups_human_output(tmp_path, capsys):
    s = serves.load_manifest_set(_set(tmp_path))
    assert serves.cmd_groups(s) == 0
    out = capsys.readouterr().out
    assert "embedding" in out and "emb, rer" in out
    assert "voice" in out and "stt, tts" in out
    assert "reserved: 'all'" in out


def test_cmd_groups_json_output(tmp_path, capsys):
    s = serves.load_manifest_set(_set(tmp_path))
    assert serves.cmd_groups(s, as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {"group": "voice", "serves": ["stt", "tts"]} in payload["groups"]
    assert "all" in payload


def test_cmd_groups_empty_when_no_groups(tmp_path, capsys):
    _write(tmp_path / "serves.toml", """
        [[serve]]
        name = "solo"
        container = "c-solo"
        port = 30001
        model = "solo-local"
        engine = "vllm"
    """)
    s = serves.load_manifest_set(str(tmp_path / "serves.toml"))
    assert serves.cmd_groups(s) == 0
    out = capsys.readouterr().out
    assert "no groups defined" in out


# ---- CLI wiring through serves.main ----------------------------------------

def test_main_groups_lists_across_set(tmp_path, capsys):
    manifest = _set(tmp_path)
    assert serves.main(["groups", "--manifest", manifest]) == 0
    out = capsys.readouterr().out
    assert "voice" in out and "stt, tts" in out
    assert "embedding" in out


def test_main_unknown_group_refuses(tmp_path, capsys):
    manifest = _set(tmp_path)
    rc = serves.main(["up", "--group", "does-not-exist", "--manifest", manifest, "--dry-run"])
    assert rc == 2
    assert "unknown group" in capsys.readouterr().err


# ---- admission still enforced per group ------------------------------------

def _absent_run():
    def run(argv, **k):
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "Error: No such object")
        return proc(0, "", "")
    return run


def test_group_up_admits_when_group_fits(tmp_path, capsys):
    s = serves.load_manifest_set(_set(tmp_path))
    targets, _ = serves.resolve_group_targets(s, ["embedding"], [])
    run = _absent_run()
    rc = serves.cmd_up(s, targets, dry_run=True, _run=run)
    assert rc == 0
    out = capsys.readouterr().out
    # emb+rer = 6000 <= 10000, no denial
    assert "reservation denied" not in out


def test_group_up_denied_when_group_over_budget(tmp_path, capsys):
    s = serves.load_manifest_set(_set(tmp_path))
    targets, _ = serves.resolve_group_targets(s, ["stack"], [])
    run = _absent_run()
    rc = serves.cmd_up(s, targets, dry_run=True, _run=run)
    assert rc == 1  # fast+emb+rer = 12000 > 10000, ledger refuses
    out = capsys.readouterr().out
    assert "reservation denied" in out
    assert "over budget" in out


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
    # Only inspect probes ran; no compose/start/stop mutation.
    assert all(c[:2] == ["docker", "inspect"] for c in calls if isinstance(c, list))


# ---- status --group keeps the whole-set ledger -----------------------------

def test_status_group_filters_rows_but_ledger_spans_set(tmp_path, capsys):
    s = serves.load_manifest_set(_set(tmp_path))

    def run(argv, **k):
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        return proc(0, "", "")

    rc = serves.cmd_status(s, names=["emb", "rer"], _run=run, _open=lambda *a, **k: (_ for _ in ()).throw(Exception()))
    assert rc == 0
    out = capsys.readouterr().out
    # Only the embedding rows are printed...
    assert "emb" in out and "rer" in out
    assert "\nfast " not in out and "heavy" not in out
    # ...but the ledger still accounts for fast's committed VRAM (whole set),
    # so committed reflects fast+emb+rer = 12000, not just the printed 6000.
    assert "committed 12000 MiB" in out


# ---- shipped manifests carry the authored groups ---------------------------

EXAMPLE_DIR = os.path.dirname(serves.EXAMPLE_MANIFEST)


def test_shipped_example_manifests_authored_groups():
    s = serves.load_manifest_set(serves.EXAMPLE_MANIFEST)
    by_name = {x["name"]: set(x.get("groups") or []) for x in s}
    assert by_name["heavy"] == {"heavy-only", "llm-stack"}
    assert by_name["fast"] == {"fast-only", "llm-stack"}
    assert by_name["embeddings"] == {"embedding", "llm-stack"}
    assert by_name["reranker"] == {"embedding", "llm-stack"}
    assert by_name["ocr"] == {"llm-stack"}
    assert by_name["vision"] == {"llm-stack"}
    assert by_name["stt"] == {"voice"}
    assert by_name["tts"] == {"voice"}
    assert by_name["comfyui"] == {"comfy"}


def test_shipped_example_experiment_serves_untagged():
    s = serves.load_manifest_set(serves.EXAMPLE_MANIFEST)
    for x in s:
        name = x["name"]
        if name.startswith("cand-") or name.startswith("voice-") or "rollback" in name:
            assert not x.get("groups"), name


def test_shipped_example_group_catalog():
    s = serves.load_manifest_set(serves.EXAMPLE_MANIFEST)
    catalog = {row["group"]: row["serves"] for row in serves.groups_summary(s)["groups"]}
    assert catalog["llm-stack"] == ["fast", "embeddings", "reranker", "ocr", "vision", "heavy"]
    assert catalog["voice"] == ["stt", "tts"]
    assert catalog["comfy"] == ["comfyui"]
