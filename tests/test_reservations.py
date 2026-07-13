"""Tests for the ADR-0017 GPU residency reservation ledger (gpu-reservations:T002).

The ledger admits or rejects `serves up` (and `voice audio up`, which delegates
to the same `cmd_up`) against per-`gpu_role` VRAM budgets declared in the
serves manifest. Docker is injected via the `_run` seam — no docker, no GPU,
no network. Mirrors tests/test_serves_manage.py's fake-`_run` style.
"""
import textwrap
import types

import pytest

from anvil_serving import reservations, serves
from anvil_serving.voice.serves import _common as voice_common


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


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
    port = 30003
    model = "fast-local"
    engine = "vllm"
    gpu_role = "dark-fast"
    vram_mib = 20480
    residency = "on-demand"
    up = "docker compose -f {dir}/compose.yml up -d fast"

    [[serve]]
    name = "stt"
    container = "anvil-voice-stt"
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
    port = 30030
    model = "plain-local"
    engine = "vllm"
    up = "docker compose -f {dir}/compose.yml up -d plain"
"""


# ---- manifest [[gpu_roles]] capacity parsing ---------------------------------

def test_load_manifest_attaches_gpu_role_budgets(tmp_path):
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    budgets = reservations.budgets_of(loaded)
    assert budgets["dark-fast"].vram_mib == 32768
    assert budgets["dark-fast"].reserve_mib == 2048
    assert budgets["dark-fast"].budget_mib == 30720


def test_load_manifest_without_gpu_roles_attaches_nothing(tmp_path):
    """T001 contract: a manifest without [[gpu_roles]] adds no keys at all."""
    loaded = serves.load_manifest(_manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30003
        model = "fast-local"
        engine = "vllm"
    """))
    assert reservations.GPU_ROLES_KEY not in loaded[0]
    assert reservations.budgets_of(loaded) == {}


@pytest.mark.parametrize("row, match", [
    ('id = "r"\nvram_mib = 0', "vram_mib must be a positive integer"),
    ('id = "r"\nvram_mib = true', "vram_mib must be a positive integer"),
    ('id = "r"\nvram_mib = "32768"', "vram_mib must be a positive integer"),
    ('id = "r"', "vram_mib must be a positive integer"),
    ('id = "r"\nvram_mib = 1024\nreserve_mib = -1',
     "reserve_mib must be a non-negative integer"),
    ('id = "r"\nvram_mib = 1024\nreserve_mib = 2048',
     "reserve_mib must not exceed vram_mib"),
    ('id = ""\nvram_mib = 1024', "id must be a non-empty string"),
    ('vram_mib = 1024', "id must be a non-empty string"),
    ('id = "r"\nvram_mib = 1024\nbogus = 1', "unknown field"),
])
def test_load_manifest_rejects_invalid_gpu_roles_rows(tmp_path, row, match):
    path = _manifest(tmp_path, """
        [[gpu_roles]]
        %s

        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30003
        model = "fast-local"
        engine = "vllm"
    """ % "\n".join("        " + line for line in row.splitlines()).lstrip())
    with pytest.raises(ValueError, match=match):
        serves.load_manifest(path)


def test_load_manifest_rejects_duplicate_gpu_role_ids(tmp_path):
    path = _manifest(tmp_path, """
        [[gpu_roles]]
        id = "dark-fast"
        vram_mib = 32768

        [[gpu_roles]]
        id = "dark-fast"
        vram_mib = 16384

        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30003
        model = "fast-local"
        engine = "vllm"
    """)
    with pytest.raises(ValueError, match="duplicate gpu_roles id"):
        serves.load_manifest(path)


# ---- over-budget `serves up` (the packet's first acceptance criterion) ------

def test_over_budget_up_exits_nonzero_prints_ledger_runs_no_container_command(
        tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    # fast (20480) + stt (4096) committed -> free 6144 < embed's 8192.
    run = _states_run({"vllm-fast": "running", "anvil-voice-stt": "running"})
    assert serves.cmd_up(loaded, ["embed"], _run=run) == 1
    assert _mutating_calls(run) == []  # NO container command ran
    out = capsys.readouterr().out
    # capacity / reserve / committed / free, per the acceptance criterion:
    assert "capacity 32768 MiB" in out
    assert "reserve 2048 MiB" in out
    assert "committed 24576 MiB" in out
    assert "free 6144 MiB" in out
    # ... and the offending reservation:
    assert "reservation denied" in out
    assert "embed 8192 MiB" in out
    assert "over budget by 2048 MiB" in out


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


def test_paused_container_still_pins_its_reservation(tmp_path):
    # A paused serve holds 100% of its VRAM -> still committed in the ledger.
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    run = _states_run({"vllm-fast": "paused", "anvil-voice-stt": "running"})
    assert serves.cmd_up(loaded, ["embed"], _run=run) == 1
    assert _mutating_calls(run) == []


# ---- in-budget and release flows ---------------------------------------------

def test_in_budget_up_starts_the_container(tmp_path):
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    # Only stt (4096) committed -> free 26624 >= embed's 8192.
    run = _states_run({"anvil-voice-stt": "running"})
    assert serves.cmd_up(loaded, ["embed"], _run=run) == 0
    assert any(c[:2] == ["docker", "compose"] for c in _mutating_calls(run))


def test_serves_down_releases_the_reservation_for_the_next_up(tmp_path):
    """`down` needs no ledger bookkeeping: a stopped container is released."""
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    states = {"vllm-fast": "running", "anvil-voice-stt": "running"}
    run = _states_run(states)
    assert serves.cmd_up(loaded, ["embed"], _run=run) == 1  # over budget
    # stop fast (docker state is the ledger's source of truth) ...
    assert serves.cmd_down(loaded, ["fast"], _run=run) == 0
    assert states["vllm-fast"] == "exited"
    # ... and the same request now fits (free 26624 >= 8192).
    assert serves.cmd_up(loaded, ["embed"], _run=run) == 0


def test_already_running_target_holds_its_own_reservation(tmp_path):
    """Re-running `up` on a running serve requests nothing new — even at 0 free."""
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    run = _states_run({
        "vllm-fast": "running", "anvil-voice-stt": "running", "vllm-embed": "running",
    })
    # committed 32768 - budget 30720 is even over-declared; a compose re-up of
    # `fast` must still be admitted (it acquires no NEW reservation).
    assert serves.cmd_up(loaded, ["fast"], _run=run) == 0


def test_batch_requests_on_one_role_are_summed(tmp_path):
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    # fast committed (20480) -> free 10240; stt (4096) + embed (8192) = 12288.
    run = _states_run({"vllm-fast": "running"})
    assert serves.cmd_up(loaded, ["stt", "embed"], _run=run) == 1
    assert _mutating_calls(run) == []


def test_reservation_on_role_without_declared_capacity_is_unenforced(tmp_path):
    """Incremental adoption: no [[gpu_roles]] capacity for the role -> no ledger."""
    loaded = serves.load_manifest(_manifest(tmp_path, """
        [[gpu_roles]]
        id = "dark-heavy"
        vram_mib = 98304

        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30003
        model = "fast-local"
        engine = "vllm"
        gpu_role = "dark-fast"
        vram_mib = 999999
        up = "docker compose -f {dir}/compose.yml up -d fast"
    """))
    run = _states_run({})
    assert serves.cmd_up(loaded, ["fast"], _run=run) == 0
    assert _mutating_calls(run) != []


# ---- in-budget flows unchanged for manifests without reservation fields ------

def test_manifest_without_reservation_fields_runs_no_extra_docker_probe(tmp_path):
    """Second acceptance criterion: pre-reservation flows are UNCHANGED —
    exactly one inspect (the target's own state check) then its `up`."""
    loaded = serves.load_manifest(_manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30003
        model = "fast-local"
        engine = "vllm"
        up = "docker compose -f {dir}/compose.yml up -d fast"
    """))
    run = _states_run({})
    assert serves.cmd_up(loaded, ["fast"], _run=run) == 0
    inspects = [c for c in run.calls if c[:2] == ["docker", "inspect"]]
    assert len(inspects) == 1  # no ledger probes were added
    assert any(c[:2] == ["docker", "compose"] for c in run.calls)


def test_targets_without_reservations_skip_the_ledger_even_with_budgets(tmp_path):
    """Bringing up a reservation-free serve never probes the other serves."""
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    run = _states_run({})
    assert serves.cmd_up(loaded, ["plain"], _run=run) == 0
    inspects = [c for c in run.calls if c[:2] == ["docker", "inspect"]]
    assert [c[-1] for c in inspects] == ["vllm-plain"]


# ---- no state file (third acceptance criterion) -------------------------------

def test_ledger_derives_from_docker_state_and_writes_no_state_file(tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    run = _states_run({"vllm-fast": "running", "anvil-voice-stt": "running"})
    serves.cmd_up(loaded, ["embed"], _run=run)
    serves.cmd_down(loaded, ["fast"], _run=run)
    after = sorted(p.name for p in tmp_path.iterdir())
    assert after == sorted(set(before) | {"serves.toml"})  # only the manifest


# ---- ledger snapshot API -------------------------------------------------------

def test_build_ledger_reports_commitments_per_role(tmp_path):
    loaded = serves.load_manifest(_manifest(tmp_path, LEDGER_MANIFEST))
    states = {"vllm-fast": "running", "anvil-voice-stt": "exited"}
    ledger = reservations.build_ledger(
        loaded, lambda container: states.get(container, "absent"))
    role = ledger["dark-fast"]
    assert role.committed_mib == 20480      # only the running serve
    assert role.free_mib == 30720 - 20480
    by_name = {r.serve: r for r in role.reservations}
    assert by_name["fast"].committed
    assert not by_name["stt"].committed     # exited -> released
    assert not by_name["embed"].committed   # absent -> never acquired
    assert by_name["fast"].residency == "on-demand"


def test_reservation_of_requires_both_gpu_role_and_vram(tmp_path):
    assert reservations.reservation_of(
        {"name": "a", "container": "c", "gpu_role": "r"}) is None
    assert reservations.reservation_of(
        {"name": "a", "container": "c", "vram_mib": 1024}) is None
    r = reservations.reservation_of(
        {"name": "a", "container": "c", "gpu_role": "r", "vram_mib": 1024})
    assert (r.serve, r.gpu_role, r.vram_mib) == ("a", "r", 1024)


# ---- voice audio up rides the same rails --------------------------------------

def test_voice_audio_up_is_denied_by_the_same_ledger(tmp_path, capsys):
    """`voice audio up` delegates to cmd_up, so admission applies unchanged."""
    manifest = _manifest(tmp_path, LEDGER_MANIFEST)
    run = _states_run({"vllm-fast": "running", "vllm-embed": "running"})
    # fast (20480) + embed (8192) committed -> free 2048 < stt's 4096.
    lifecycle = voice_common.ServeLifecycle("stt", manifest_path=manifest, _run=run)
    assert lifecycle.bring_up() == 1
    assert _mutating_calls(run) == []
    assert "reservation denied" in capsys.readouterr().out


def test_voice_audio_up_in_budget_starts_the_serve(tmp_path):
    manifest = _manifest(tmp_path, LEDGER_MANIFEST)
    run = _states_run({"vllm-fast": "running"})
    lifecycle = voice_common.ServeLifecycle("stt", manifest_path=manifest, _run=run)
    assert lifecycle.bring_up() == 0
    assert any(c[:2] == ["docker", "compose"] for c in _mutating_calls(run))
