"""Tests for the `serves` management verbs — rm / adopt / up --compose.

Docker is injected via the module's `_run` seam, so these run with no docker, no
GPU, and no network. Mirrors tests/test_serves.py's fake-`_run` style.
"""
import textwrap
import types

from anvil_serving import serves


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _manifest(tmp_path, body):
    p = tmp_path / "serves.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def _inspect_returning(state, op_rc=0, op_err=""):
    """A fake _run: `docker inspect` -> `state`; any other command -> proc(op_rc)."""
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            if state == "absent":
                return proc(1, "", "Error: No such object")
            if state == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            return proc(0, state + "\n")
        return proc(op_rc, "", op_err)

    run.calls = calls
    return run


# ---- rm ---------------------------------------------------------------------

def test_cmd_rm_removes_manifest_serve_by_name():
    # a token matching a manifest serve's name resolves to that serve's container.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["heavy"], assume_yes=True, _run=run) == 0
    assert ["docker", "rm", "-f", "sglang"] in run.calls


def test_cmd_rm_removes_literal_non_manifest_container():
    # THE key case: a container NOT in the manifest (experiment squatting a port) is
    # removed by its literal name — no manifest entry required.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["vllm-experiment"], assume_yes=True, _run=run) == 0
    assert ["docker", "rm", "-f", "vllm-experiment"] in run.calls


def test_cmd_rm_absent_container_is_noop_success(capsys):
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("absent")
    assert serves.cmd_rm(serv, ["ghost"], assume_yes=True, _run=run) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)  # nothing removed
    assert "nothing to remove" in capsys.readouterr().out


def test_cmd_rm_error_state_is_not_false_success():
    # docker daemon unreachable -> cannot remove, must NOT claim rc 0.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("error")
    assert serves.cmd_rm(serv, ["sglang"], assume_yes=True, _run=run) == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)


def test_cmd_rm_reports_remove_failure():
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running", op_rc=1, op_err="boom")
    assert serves.cmd_rm(serv, ["sglang"], assume_yes=True, _run=run) == 1


def test_cmd_rm_dry_run_removes_nothing(capsys):
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["sglang"], dry_run=True, assume_yes=True, _run=run) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert "rm -f sglang" in capsys.readouterr().out  # printed the plan


def test_cmd_rm_no_names_errors():
    assert serves.cmd_rm([], [], assume_yes=True, _run=_inspect_returning("running")) == 1


def test_cmd_rm_ambiguous_token_refuses(capsys):
    # token "shared" is serve A's NAME and serve B's CONTAINER -> ambiguous -> refuse (rc 1)
    # and remove NOTHING, rather than destroy an untargeted serve (Greptile #373).
    serv = [{"name": "shared", "container": "cont-a", "port": 1, "health": "/health"},
            {"name": "b", "container": "shared", "port": 2, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["shared"], assume_yes=True, _run=run) == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)  # removed nothing
    assert "ambiguous" in capsys.readouterr().out


# ---- adopt ------------------------------------------------------------------

def test_cmd_adopt_recreates_manifest_serve_under_compose(capsys):
    # adopt an externally-started (running) compose serve -> `docker rm -f` + `up`.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local",
             "up": ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]}]
    run = _inspect_returning("running")
    assert serves.cmd_adopt(serv, ["heavy"], assume_yes=True, _run=run) == 0
    # ORDER matters: `docker rm -f` MUST precede `up` (the whole point of recreate — a
    # reordered/up-before-rm regression would leave the stale container or name-clash).
    i_rm = run.calls.index(["docker", "rm", "-f", "sglang"])
    i_up = run.calls.index(["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"])
    assert i_rm < i_up
    out = capsys.readouterr().out
    assert "adopting heavy" in out


def test_cmd_adopt_no_match_errors():
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_adopt(serv, ["nope"], assume_yes=True, _run=run) == 1


def test_cmd_adopt_dry_run_touches_nothing(capsys):
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local",
             "up": ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]}]
    run = _inspect_returning("running")
    assert serves.cmd_adopt(serv, ["heavy"], dry_run=True, assume_yes=True, _run=run) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert not any(c[:2] == ["docker", "compose"] for c in run.calls)
    assert "adopting heavy" in capsys.readouterr().out


# ---- down --dry-run (safety: a preview must NOT stop serving) ----------------

def test_cmd_down_dry_run_does_not_stop(capsys):
    # `down` frees GPUs / kills in-flight serving, so --dry-run must only PRINT the plan.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_down(serv, ["heavy"], dry_run=True, _run=run) == 0
    assert not any(c[:2] == ["docker", "stop"] for c in run.calls)  # nothing stopped
    assert "stop sglang" in capsys.readouterr().out                 # printed the plan


# ---- up --compose -----------------------------------------------------------

def test_cmd_up_compose_runs_compose_up_argv():
    calls = []

    def run(argv, **k):
        calls.append(argv)
        return proc(0)

    assert serves.cmd_up_compose("/x/experiment.yml", ["svc-a", "svc-b"], _run=run) == 0
    assert calls == [["docker", "compose", "-f", "/x/experiment.yml",
                      "up", "-d", "svc-a", "svc-b"]]


def test_cmd_up_compose_no_services_brings_up_whole_file():
    calls = []

    def run(argv, **k):
        calls.append(argv)
        return proc(0)

    assert serves.cmd_up_compose("/x/experiment.yml", [], _run=run) == 0
    assert calls == [["docker", "compose", "-f", "/x/experiment.yml", "up", "-d"]]


def test_cmd_up_compose_reports_failure():
    def run(argv, **k):
        return proc(1, "", "compose blew up")
    assert serves.cmd_up_compose("/x/experiment.yml", [], _run=run) == 1


def test_cmd_up_compose_dry_run_runs_nothing(capsys):
    calls = []

    def run(argv, **k):
        calls.append(argv)
        return proc(0)

    assert serves.cmd_up_compose("/x/experiment.yml", ["svc"], dry_run=True, _run=run) == 0
    assert calls == []  # nothing executed
    assert "/x/experiment.yml" in capsys.readouterr().out  # printed the plan


# ---- main() dispatch --------------------------------------------------------
#
# The cmd_* functions capture `_run=subprocess.run` as a def-time default, so these
# dispatch tests patch the cmd_* functions themselves (proving routing) rather than
# subprocess — no real docker is touched.

def test_main_up_compose_needs_no_manifest(tmp_path, monkeypatch):
    # `up --compose` is independent of serves.toml: it dispatches BEFORE the manifest is
    # loaded, so a missing serves.toml does not error out (rc 2 for missing manifest).
    monkeypatch.chdir(tmp_path)  # no serves.toml here
    seen = {}

    def fake(compose_file, services, dry_run=False):
        seen["compose"], seen["services"], seen["dry_run"] = compose_file, services, dry_run
        return 0

    monkeypatch.setattr(serves, "cmd_up_compose", fake)
    rc = serves.main(["up", "--compose", "/x/experiment.yml", "svc-a", "svc-b"])
    assert rc == 0
    assert seen == {"compose": "/x/experiment.yml", "services": ["svc-a", "svc-b"],
                    "dry_run": False}


def test_main_compose_rejected_for_non_up_action(capsys):
    # --compose only means something for `up`; using it with any other action is a usage error.
    rc = serves.main(["down", "--compose", "/x/experiment.yml"])
    assert rc == 2
    assert "only valid with `up`" in capsys.readouterr().err


def test_main_compose_with_recreate_rejected(capsys):
    # --recreate is meaningless with --compose (compose up -d already recreates on change).
    rc = serves.main(["up", "--compose", "/x/experiment.yml", "--recreate"])
    assert rc == 2
    assert "--recreate" in capsys.readouterr().err


def test_main_rm_dispatches(tmp_path, monkeypatch):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "heavy"
        container = "sglang"
        port = 30000
        model = "heavy-local"
        engine = "sglang"
    """)
    seen = {}

    def fake(serves_list, names, dry_run=False, assume_yes=False):
        seen["names"], seen["dry_run"] = names, dry_run
        seen["assume_yes"] = assume_yes
        return 0

    monkeypatch.setattr(serves, "cmd_rm", fake)
    rc = serves.main(["rm", "port-squatter", "--dry-run", "--yes", "--manifest", path])
    assert rc == 0
    assert seen == {"names": ["port-squatter"], "dry_run": True, "assume_yes": True}


def test_main_adopt_dispatches(tmp_path, monkeypatch):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "heavy"
        container = "sglang"
        port = 30000
        model = "heavy-local"
        engine = "sglang"
    """)
    seen = {}

    def fake(serves_list, names, dry_run=False, assume_yes=False):
        seen["names"], seen["dry_run"] = names, dry_run
        seen["assume_yes"] = assume_yes
        return 0

    monkeypatch.setattr(serves, "cmd_adopt", fake)
    rc = serves.main(["adopt", "heavy", "--manifest", path])
    assert rc == 0
    assert seen == {"names": ["heavy"], "dry_run": False, "assume_yes": False}


# ---- logs -------------------------------------------------------------------

_TWO = [{"name": "heavy", "container": "vllm-heavy"}, {"name": "fast", "container": "vllm-fast"}]


def test_serves_logs_resolves_name_and_prints(capsys):
    def fake(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        return proc(0, "HEAVY LOG\n", "warn\n")
    rc = serves.cmd_logs(_TWO, ["heavy"], tail="5", _run=fake)
    assert rc == 0
    out = capsys.readouterr()
    assert "HEAVY LOG" in out.out and "warn" in out.err


def test_serves_logs_argv_targets_the_named_container():
    seen = {}
    def fake(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        seen["argv"] = argv
        return proc(0)
    serves.cmd_logs(_TWO, ["fast"], _run=fake)
    assert seen["argv"][:2] == ["docker", "logs"] and seen["argv"][-1] == "vllm-fast"


def test_serves_logs_requires_a_name(capsys):
    # `logs` targets ONE serve, so no name is an error — NOT "all" (which would pick the sole
    # serve on a 1-serve manifest but error on a 2-serve one). Never touches docker.
    rc = serves.cmd_logs(_TWO, [], _run=lambda *a, **k: proc(0, "running\n"))
    assert rc == 2
    assert "needs a serve name" in capsys.readouterr().err


def test_serves_logs_multiple_names_refuses(capsys):
    rc = serves.cmd_logs(_TWO, ["heavy", "fast"], _run=lambda *a, **k: proc(0, "running\n"))
    assert rc == 2
    assert "ONE serve" in capsys.readouterr().err


def test_serves_logs_no_match_errors(capsys):
    rc = serves.cmd_logs(_TWO, ["nope"], _run=lambda *a, **k: proc(0))
    assert rc == 1
    assert "no matching serve" in capsys.readouterr().err


def test_serves_logs_absent_container(capsys):
    def fake(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "No such object")
        return proc(0)
    rc = serves.cmd_logs([{"name": "heavy", "container": "c"}], ["heavy"], _run=fake)
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


def test_serves_logs_dispatched_from_main(tmp_path, monkeypatch):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "heavy"
        container = "vllm-heavy"
        port = 30002
        model = "heavy-local"
        engine = "vllm"
        base_url = "http://127.0.0.1:30002/v1"
    """)
    seen = {}
    monkeypatch.setattr(serves, "cmd_logs",
                        lambda s, names, **k: seen.update(names=names, **k) or 0)
    rc = serves.main(["logs", "heavy", "--tail", "3", "--manifest", path])
    assert rc == 0 and seen["names"] == ["heavy"] and seen["tail"] == "3"


# ---- confirm gate (guard.confirm) -------------------------------------------

def test_cmd_rm_prompt_declined_removes_nothing(capsys):
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    rc = serves.cmd_rm(serv, ["sglang"], _run=run, _input=lambda p: "n")
    assert rc == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert "aborted" in capsys.readouterr().out


def test_cmd_rm_no_tty_answers_no():
    # EOF (piped/automation without --yes) must fail-safe to No.
    def eof(_prompt):
        raise EOFError
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["sglang"], _run=run, _input=eof) == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)


def test_cmd_rm_dry_run_needs_no_confirmation():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    def explode(_prompt):
        raise AssertionError("dry-run must not prompt")
    assert serves.cmd_rm(serv, ["sglang"], dry_run=True, _run=run, _input=explode) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)


def test_cmd_adopt_prompt_declined_recreates_nothing(capsys):
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health",
             "up": ["docker", "compose", "-f", "x.yml", "up", "-d", "sglang"]}]
    run = _inspect_returning("running")
    rc = serves.cmd_adopt(serv, ["h"], _run=run, _input=lambda p: "")
    assert rc == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert "aborted" in capsys.readouterr().out
