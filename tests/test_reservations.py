"""Tests for the ADR-0017 GPU residency reservation ledger (gpu-reservations:T002).

The ledger admits or rejects `serves up` (and `voice audio up`, which delegates
to the same `cmd_up`) against per-`gpu_role` VRAM budgets declared in the
serves manifest. Docker is injected via the `_run` seam — no docker, no GPU,
no network. Mirrors tests/test_serves_manage.py's fake-`_run` style.
"""
import json
import textwrap

import pytest

from anvil_serving import serves
from tests.conftest import proc


def _manifest(tmp_path, body):
    p = tmp_path / "serves.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


MUTATING = (["docker", "start"], ["docker", "rm"], ["docker", "unpause"],
            ["docker", "compose"], ["docker-compose"], ["docker", "stop"], ["bash"])


def _mutating_calls(run):
    return [c for c in run.calls
            if isinstance(c, list) and any(c[:len(p)] == p for p in MUTATING)]


def _states_run(states, op_rc=0):
    """A fake _run: `docker inspect <container>` -> states[container]
    ('absent'/'error' modeled as docker inspect failures); `docker stop` flips
    the container to 'exited' (so cmd_down's stop-stuck re-inspect passes);
    anything else -> ok.
    """
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:3] == ["docker", "ps", "-a"]:
            if any(state == "error" for state in states.values()):
                return proc(1, "", "Cannot connect to the Docker daemon")
            rows = [
                json.dumps({"Names": name, "State": state})
                for name, state in states.items()
                if state != "absent"
            ]
            return proc(0, "\n".join(rows))
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            state = states.get(argv[-1], "absent")
            if state == "absent":
                return proc(1, "", "Error: No such object")
            if state == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            return proc(0, state + "\n")
        if isinstance(argv, list) and argv[:2] == ["docker", "stop"] and op_rc == 0:
            states[argv[-1]] = "exited"
        return proc(op_rc)

    run.calls = calls
    return run


# The reference multi-tenant card: 32 GiB 5090, 2 GiB display reserve ->
# 30720 MiB budget. `fast` (20 GiB) + `stt` (4 GiB) resident leaves 6.5 GiB.
LEDGER_MANIFEST = """
    [[gpu_roles]]
    id = "dark-fast"
    vram_mib = 32768
    reserve_mib = 2048

    [[serve]]
    name = "fast"
    container = "vllm-fast"
    runtime = "docker"
    port = 30003
    model = "auxiliary-local"
    engine = "vllm"
    gpu_role = "dark-fast"
    vram_mib = 20480
    residency = "on-demand"
    up = "docker compose -f {dir}/compose.yml up -d fast"

    [[serve]]
    name = "stt"
    container = "anvil-voice-stt"
    runtime = "docker"
    port = 30010
    model = "tdt_ctc-110m"
    engine = "audio"
    gpu_role = "dark-fast"
    vram_mib = 4096
    residency = "resident"
    up = "docker compose -f {dir}/compose.yml up -d stt"

    [[serve]]
    name = "embed"
    container = "vllm-embed"
    runtime = "docker"
    port = 30020
    model = "qwen3-embedding-4b"
    engine = "vllm"
    gpu_role = "dark-fast"
    vram_mib = 8192
    residency = "resident"
    up = "docker compose -f {dir}/compose.yml up -d embed"

    [[serve]]
    name = "plain"
    container = "vllm-plain"
    runtime = "docker"
    port = 30030
    model = "plain-local"
    engine = "vllm"
    up = "docker compose -f {dir}/compose.yml up -d plain"
"""


# ---- manifest [[gpu_roles]] capacity parsing ---------------------------------


# ---- over-budget `serves up` (the packet's first acceptance criterion) ------


def test_over_budget_up_denies_the_whole_batch(tmp_path):
    """One over-budget target fails the batch before ANY serve is started."""
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    run = _states_run({"vllm-fast": "running", "anvil-voice-stt": "running"})
    # `plain` alone would start fine; batched with over-budget `embed` nothing runs.
    assert serves.cmd_up(loaded, ["plain", "embed"], _run=run) == 1
    assert _mutating_calls(run) == []


def test_over_budget_dry_run_also_shows_the_denial(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    run = _states_run({"vllm-fast": "running", "anvil-voice-stt": "running"})
    assert serves.cmd_up(loaded, ["embed"], dry_run=True, _run=run) == 1
    assert "reservation denied" in capsys.readouterr().out


# ---- in-budget and release flows ---------------------------------------------


# ---- in-budget flows unchanged for manifests without reservation fields ------


# ---- no state file (third acceptance criterion) -------------------------------


# ---- ledger snapshot API -------------------------------------------------------


# ---- engine-enforced budgets (gpu-reservations:T003, ADR-0017 §4) -------------


# ---- voice audio up rides the same rails --------------------------------------


# ---- eviction through the ADR-0018 drain (gpu-reservations:T005) --------------

# `fast` is the on-demand LLM tier; `exp` is a routed evictable experiment
# serve; `stt` is a resident sidecar. With exp (16384) + stt (4096) committed,
# free is 10240 < fast's 20480 — evicting exp admits fast.
EVICTION_MANIFEST = """
    [[gpu_roles]]
    id = "dark-fast"
    vram_mib = 32768
    reserve_mib = 2048

    [[serve]]
    name = "fast"
    container = "vllm-fast"
    runtime = "docker"
    port = 30003
    model = "auxiliary-local"
    engine = "vllm"
    gpu_role = "dark-fast"
    vram_mib = 20480
    residency = "on-demand"
    up = "docker compose -f {dir}/compose.yml up -d fast"

    [[serve]]
    name = "exp"
    container = "vllm-exp"
    runtime = "docker"
    port = 30040
    model = "exp-local"
    engine = "vllm"
    gpu_role = "dark-fast"
    vram_mib = 16384
    residency = "evictable"
    router_tier = "exp-local"
    up = "docker compose -f {dir}/compose.yml up -d exp"

    [[serve]]
    name = "stt"
    container = "anvil-voice-stt"
    runtime = "docker"
    port = 30010
    model = "tdt_ctc-110m"
    engine = "audio"
    gpu_role = "dark-fast"
    vram_mib = 4096
    residency = "resident"
    up = "docker compose -f {dir}/compose.yml up -d stt"
"""

# The deficit (10240) exceeds every evictable reservation combined (4096):
# the rest is held by the `resident` stt, which is never a candidate.
RESIDENT_HELD_MANIFEST = EVICTION_MANIFEST.replace(
    'vram_mib = 16384\n    residency = "evictable"',
    'vram_mib = 4096\n    residency = "evictable"',
).replace(
    'vram_mib = 4096\n    residency = "resident"',
    'vram_mib = 16384\n    residency = "resident"',
)


def _journal_run(states, journal):
    """_states_run plus an ordered journal of `docker stop` mutations, so a
    test can prove drain (journaled by the transition seam) precedes stop."""
    inner = _states_run(states)

    def run(argv, **kwargs):
        if isinstance(argv, list) and argv[:2] == ["docker", "stop"]:
            journal.append(("stop", argv[-1]))
        return inner(argv, **kwargs)

    run.calls = inner.calls
    return run


def _recording_transition(journal, refuse=()):
    """An ADR-0018 step seam that journals each action (returncode semantics)."""
    def transition(action, tier_id, timeout=None):
        journal.append(
            (action, tier_id) if timeout is None else (action, tier_id, timeout))
        return 1 if action in refuse else 0

    return transition


def test_evicting_resident_serves_fails_loudly_with_the_ledger(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, RESIDENT_HELD_MANIFEST))
    journal = []
    run = _journal_run(
        {"vllm-exp": "running", "anvil-voice-stt": "running"}, journal)
    rc = serves.cmd_up(loaded, ["fast"], evict=True,
                       _transition=_recording_transition(journal), _run=run)
    assert rc == 1
    assert journal == []                    # no transition, no stop
    assert _mutating_calls(run) == []       # NO container command ran
    out = capsys.readouterr().out
    assert "eviction refused" in out
    assert "every evictable reservation combined frees only 4096 MiB" in out
    # ... loudly, WITH the ledger and the protected resident reservation:
    assert "capacity 32768 MiB" in out
    assert "free 10240 MiB" in out
    assert "never evicted (residency 'resident'): stt 16384 MiB" in out


def test_drain_refusal_aborts_eviction_before_any_container_mutation(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, EVICTION_MANIFEST))
    states = {"vllm-exp": "running", "anvil-voice-stt": "running"}
    journal = []
    run = _journal_run(states, journal)
    rc = serves.cmd_up(loaded, ["fast"], evict=True, drain_timeout=7,
                       _transition=_recording_transition(journal, refuse=("drain",)),
                       _run=run)
    assert rc == 2
    assert _mutating_calls(run) == []       # the victim was NOT stopped
    assert states["vllm-exp"] == "running"
    # ... and the quiesced tier was compensated with a guarded readmit.
    assert journal == [
        ("quiesce", "exp-local"), ("drain", "exp-local", 7), ("readmit", "exp-local"),
    ]
    assert "drain timed out for exp-local before container mutation" in (
        capsys.readouterr().out)


def test_quiesce_refusal_compensates_and_stops_nothing(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, EVICTION_MANIFEST))
    journal = []
    run = _journal_run(
        {"vllm-exp": "running", "anvil-voice-stt": "running"}, journal)
    rc = serves.cmd_up(loaded, ["fast"], evict=True,
                       _transition=_recording_transition(journal, refuse=("quiesce",)),
                       _run=run)
    assert rc == 2
    assert _mutating_calls(run) == []
    assert journal == [("quiesce", "exp-local"), ("readmit", "exp-local")]
    assert "failed to quiesce exp-local" in capsys.readouterr().out


def test_evict_dry_run_previews_the_transition_without_stopping(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, EVICTION_MANIFEST))
    journal = []
    run = _journal_run(
        {"vllm-exp": "running", "anvil-voice-stt": "running"}, journal)
    rc = serves.cmd_up(loaded, ["fast"], evict=True, dry_run=True,
                       _transition=_recording_transition(journal), _run=run)
    assert rc == 0
    assert journal == []                    # previewed, not executed
    assert _mutating_calls(run) == []
    out = capsys.readouterr().out
    assert "evict exp: quiesce + drain router tier exp-local" in out
    assert "up fast" in out                 # the acquisition preview continues


def test_evict_rejects_a_non_positive_drain_timeout(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, EVICTION_MANIFEST))
    run = _states_run({"vllm-exp": "running", "anvil-voice-stt": "running"})
    assert serves.cmd_up(loaded, ["fast"], evict=True, drain_timeout=0,
                         _run=run) == 2
    assert run.calls == []                  # not even a probe ran
    assert "--drain-timeout" in capsys.readouterr().out


@pytest.mark.parametrize(
    "body, message",
    [
        (
            'gpu_roles = ["dark-compute-a", "dark-compute-a"]\n'
            'operating_mode = "dual-gpu-exclusive"\n'
            'tensor_parallel_size = 2',
            "must be distinct",
        ),
        (
            'gpu_roles = ["dark-compute-a", "dark-compute-b"]\n'
            'operating_mode = "dual-gpu-exclusive"\n'
            'tensor_parallel_size = 1',
            "tensor_parallel_size = 2",
        ),
    ],
)
def test_dual_gpu_exclusive_manifest_fails_closed(tmp_path, body, message):
    with pytest.raises(ValueError, match=message):
        serves.load_manifest(_manifest(tmp_path, f"""
            [[gpu_roles]]
            id = "dark-compute-a"
            vram_mib = 97887

            [[gpu_roles]]
            id = "dark-compute-b"
            vram_mib = 97887

            [[serve]]
            name = "tp2"
            container = "vllm-tp2"
            runtime = "docker"
            port = 30000
            model = "candidate-local"
            engine = "vllm"
            vram_mib = 90000
            {body}
        """))
