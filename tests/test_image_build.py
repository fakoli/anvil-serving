"""Resource ceilings and failure boundaries for the managed image builder."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving import image_build


@pytest.fixture
def config(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    path = tmp_path / "image-builds.toml"
    path.write_text('''schema = "anvil-serving.image-builds/v1"
[builds.worker]
context = "."
dockerfile = "Dockerfile"
image = "anvil-worker:candidate"
cpus = 2
memory_mib = 8192
''', encoding="utf-8")
    return path


def test_preview_is_side_effect_free_with_explicit_limits(config, tmp_path):
    plan = image_build.build_plan("worker", config)
    def forbidden(*args, **kwargs):
        pytest.fail("preview invoked Docker")
    result = image_build.execute_build(plan, runner=forbidden, log_dir=tmp_path / "logs")
    assert result["dry_run"] and result["ok"]
    assert not (tmp_path / "logs").exists()
    assert "memory=8192m" in result["argv"]
    assert "memory-swap=8192m" in result["argv"]
    assert "cpu-period=100000" in result["argv"]
    assert "cpu-quota=200000" in result["argv"]


@pytest.mark.parametrize(("old", "new"), [
    ("cpus = 2", "cpus = true"), ("cpus = 2", "cpus = inf"),
    ("cpus = 2", "cpus = 0"), ("cpus = 2", "cpus = 16"),
    ("memory_mib = 8192", "memory_mib = -1"),
    ("memory_mib = 8192", "memory_mib = 65536"),
    ('image = "anvil-worker:candidate"', 'image = "--push"'),
    ('dockerfile = "Dockerfile"', 'dockerfile = "../Dockerfile"'),
    ('[builds.worker]', '[builds.worker]\nsecret = "not-accepted"'),
])
def test_invalid_declaration_refuses_before_docker(config, old, new):
    config.write_text(config.read_text().replace(old, new))
    with pytest.raises(image_build.ImageBuildError):
        image_build.build_plan("worker", config)


def test_parallel_stages_are_rejected(config):
    (config.parent / "Dockerfile").write_text("FROM scratch AS first\nFROM scratch\n")
    with pytest.raises(image_build.ImageBuildError, match="single-stage"):
        image_build.build_plan("worker", config)


def test_old_cli_cannot_fall_back_to_unbounded_build(config, tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="old build help")
    with pytest.raises(image_build.ImageBuildError, match="lacks --resource"):
        image_build.execute_build(image_build.build_plan("worker", config), confirm=True,
                                  runner=run, log_dir=tmp_path / "logs")
    assert calls == [["docker", "buildx", "build", "--help"]]
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("mode", ["success", "failure", "timeout", "missing-id"])
def test_build_outcomes_never_operate_a_serve(config, tmp_path, mode):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "--help":
            return SimpleNamespace(returncode=0, stdout="--resource")
        if argv[1] == "info":
            return SimpleNamespace(returncode=0, stdout="linux\n")
        if argv[1:3] == ["image", "ls"]:
            return SimpleNamespace(returncode=0, stdout="")
        pytest.fail("unexpected Docker operation")
    def build(argv, **kwargs):
        calls.append(argv)
        assert argv[:3] == ["docker", "buildx", "build"]
        assert "--resource" in argv and "--load" in argv and "--push" not in argv
        assert argv[argv.index("--file") + 1] == "-"
        assert kwargs["source_bytes"] == (config.parent / "Dockerfile").read_bytes()
        kwargs["log"].write(b"bounded build diagnostics\n")
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, 30)
        if mode == "success":
            Path(argv[argv.index("--iidfile") + 1]).write_text("sha256:" + "a" * 64)
        return {"returncode": 1 if mode == "failure" else 0}
    def directory(path, runner):
        path.mkdir()
        return path
    result = image_build.execute_build(image_build.build_plan("worker", config), confirm=True,
                                      runner=run, build_runner=build, directory_factory=directory,
                                      log_dir=tmp_path / "logs")
    assert result["ok"] == (mode == "success")
    assert len(calls) == 4
    assert Path(result["log_path"]).read_text() == "bounded build diagnostics\n"
    assert result["image_id"] == ("sha256:" + "a" * 64 if mode == "success" else None)


def test_dry_run_overrides_confirmation(config, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run invoked Docker")
    result = image_build.execute_build(image_build.build_plan("worker", config), confirm=True,
                                      dry_run=True, runner=forbidden, log_dir=tmp_path)
    assert result["dry_run"]


def test_changed_dockerfile_cannot_expand_validated_build(config, tmp_path):
    plan = image_build.build_plan("worker", config)
    (config.parent / "Dockerfile").write_text("FROM scratch\nFROM scratch\n")
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="--resource" if "--help" in argv else ("linux" if argv[1] == "info" else ""))
    with pytest.raises(image_build.ImageBuildError, match="changed after"):
        image_build.execute_build(plan, confirm=True, runner=run, log_dir=tmp_path / "logs")
    assert not (tmp_path / "logs").exists()


def test_stream_retains_only_bounded_output_and_feeds_exact_stdin(tmp_path):
    with (tmp_path / "output.log").open("wb") as log:
        result = image_build._stream_build(
            [sys.executable, "-c", "import sys; assert sys.stdin.buffer.read() == b'bound source'; sys.stdout.buffer.write(b'x'*100000)"],
            source_bytes=b"bound source", log=log, timeout=10, max_log_bytes=1024,
        )
    assert result == {"returncode": 0, "log_truncated": True}
    assert (tmp_path / "output.log").stat().st_size == 1024


def test_private_directory_is_unique_and_protected(tmp_path):
    import os
    if os.name == "nt":
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout='"operator","S-1-5-21-1000"')
        path = image_build._private_run_directory(tmp_path / "logs", run)
        assert calls[1][0] == "icacls"
        assert "/inheritance:r" in calls[1]
        assert "*S-1-5-21-1000:(OI)(CI)F" in calls[1]
    else:
        path = image_build._private_run_directory(tmp_path / "logs", None)
        assert path.stat().st_mode & 0o777 == 0o700
    assert path.is_dir()


def test_timeout_terminates_child_that_inherits_the_output_pipe(tmp_path):
    import time
    marker = tmp_path / "orphan.txt"
    child = "import time; from pathlib import Path; time.sleep(1.5); Path(%r).write_text('orphan')" % str(marker)
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); print('spawned',flush=True); time.sleep(30)" % child
    with (tmp_path / "timeout.log").open("wb") as log:
        result = image_build._stream_build([sys.executable, "-c", parent],
                                           source_bytes=b"", log=log, timeout=0.5)
    assert "timed out" in result["error"]
    assert "daemon_state_unverified" in result["cancellation"]
    assert not result.get("output_error")
    time.sleep(1.6)
    assert not marker.exists()


def test_log_root_is_private_under_shared_umask_and_rejects_unsafe_existing_root(tmp_path):
    import os
    if os.name == "nt":
        pytest.skip("POSIX directory permissions")
    logs = tmp_path / "logs"
    previous = os.umask(0o002)
    try:
        image_build._private_run_directory(logs, None)
    finally:
        os.umask(previous)
    assert logs.stat().st_mode & 0o777 == 0o700
    logs.chmod(0o775)
    with pytest.raises(image_build.ImageBuildError, match="writable by other users"):
        image_build._private_run_directory(logs, None)
    assert logs.stat().st_mode & 0o777 == 0o775


def test_host_guard_requires_explicit_pair(config):
    config.write_text(config.read_text() + 'min_host_available_mib = 20\n')
    with pytest.raises(image_build.ImageBuildError, match='both positive'):
        image_build.build_plan('worker', config)
    config.write_text(config.read_text() + 'min_context_free_mib = 120\n')
    if sys.platform == 'linux':
        plan = image_build.build_plan('worker', config)
        assert plan['min_host_available_mib'] == 20 and plan['min_context_free_mib'] == 120
    else:
        with pytest.raises(image_build.ImageBuildError, match='Linux'):
            image_build.build_plan('worker', config)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux-only guard')
@pytest.mark.parametrize(('observer_mode', 'build_sleep', 'timeout', 'expected'), [
    ('normal', 0.3, 5, None), ('floor', 0.3, 5, 'memory below floor'),
    ('disk', 0.3, 5, 'free space below floor'), ('death', 5, 5, 'host observer'),
    ('stale', 5, 5, 'heartbeat gap'), ('hang', 8, 8, 'heartbeat gap'), ('normal', 5, 0.4, 'timed out'),
])
def test_guarded_build_owner_observer_cancellation(tmp_path, monkeypatch, observer_mode, build_sleep, timeout, expected):
    from anvil_serving import reasoning_probe
    code = '''import json,time,sys
mode = %r
def send(a=2097152,d=2097152,t=None):
 print(json.dumps({'available_bytes':a,'free_bytes':d,'monotonic':time.monotonic() if t is None else t}),flush=True)
if mode == 'floor': send(a=0)
elif mode == 'disk': send(d=0)
else:
 send()
 if mode == 'death': time.sleep(.1); sys.exit(1)
 if mode == 'stale': time.sleep(.1); send(t=time.monotonic()-6)
 if mode == 'hang': time.sleep(7)
 while True: time.sleep(.1); send()
''' % observer_mode
    monkeypatch.setattr(reasoning_probe, '_read_bytes', lambda _: code.encode())
    guard = {'context': str(tmp_path), 'min_host_available_mib': 1, 'min_context_free_mib': 1}
    with (tmp_path / 'output').open('wb') as log:
        result = image_build._stream_build([sys.executable, '-c', f'import time; time.sleep({build_sleep})'],
                  source_bytes=b'', log=log, timeout=timeout, guard=guard)
    if expected is None:
        assert result['returncode'] == 0 and result['min_available_bytes'] == 2097152
    else:
        assert expected in result['error']
        if observer_mode not in ('floor', 'disk'):
            assert 'daemon_state_unverified' in result['cancellation']
        else:
            assert 'cancellation' not in result  # Refused before client start.


@pytest.mark.parametrize(("status", "output", "reason"), [
    (0, "sha256:" + "a" * 64 + "\n", "already exists"),
    (0, "invalid\n", "unknown"), (1, "", "unknown"),
])
def test_prebuild_tag_guard_refuses_existing_or_unknown(config, tmp_path, status, output, reason):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "--help": return SimpleNamespace(returncode=0, stdout="--resource")
        if argv[1] == "info": return SimpleNamespace(returncode=0, stdout="linux")
        assert argv == ["docker", "image", "ls", "--quiet", "--no-trunc", "--filter", "reference=anvil-worker:candidate"]
        return SimpleNamespace(returncode=status, stdout=output)
    with pytest.raises(image_build.ImageBuildError, match=reason):
        image_build.execute_build(image_build.build_plan('worker', config), confirm=True,
                                  runner=run, log_dir=tmp_path / 'logs')
    assert not (tmp_path / 'logs').exists()


def test_observer_sample_validates_required_inputs(monkeypatch):
    import io
    from anvil_serving import image_build_observer
    monkeypatch.setattr('builtins.open', lambda *a, **k: io.StringIO('MemAvailable: 234 kB\n'))
    monkeypatch.setattr(image_build_observer.os, 'statvfs', lambda _: SimpleNamespace(f_bavail=4, f_frsize=4096))
    sample = image_build_observer.sample('/ignored')
    assert sample['available_bytes'] == 234 * 1024 and sample['free_bytes'] == 16384
    monkeypatch.setattr('builtins.open', lambda *a, **k: io.StringIO('MemTotal: 234 kB\n'))
    with pytest.raises(ValueError, match='missing memory'):
        image_build_observer.sample('/ignored')


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux-only guard')
def test_guarded_build_interruption_retains_unknown_daemon_state(tmp_path, monkeypatch):
    from anvil_serving import reasoning_probe
    code = b"import json,time\nprint(json.dumps({'available_bytes':2097152,'free_bytes':2097152,'monotonic':time.monotonic()}),flush=True)\ntime.sleep(10)\n"
    monkeypatch.setattr(reasoning_probe, '_read_bytes', lambda _: code)
    native_selector = image_build.selectors.DefaultSelector
    class InterruptSelector:
        def __init__(self):
            self.selector = native_selector()
            self.calls = 0
        def register(self, *args): return self.selector.register(*args)
        def close(self): return self.selector.close()
        def select(self, timeout):
            self.calls += 1
            if self.calls >= 3: raise KeyboardInterrupt()
            return self.selector.select(timeout)
    monkeypatch.setattr(image_build.selectors, 'DefaultSelector', InterruptSelector)
    with (tmp_path / 'output').open('wb') as log:
        result = image_build._stream_build([sys.executable, '-c', 'import time; time.sleep(10)'],
            source_bytes=b'', log=log, timeout=5,
            guard={'context': str(tmp_path), 'min_host_available_mib': 1, 'min_context_free_mib': 1})
    assert result['returncode'] == -1 and 'KeyboardInterrupt' in result['error']
    assert 'daemon_state_unverified' in result['cancellation']


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux-only guard')
def test_guarded_reader_start_failure_stops_both_owned_processes(tmp_path, monkeypatch):
    from anvil_serving import reasoning_probe
    code = b"import json,time\nprint(json.dumps({'available_bytes':2097152,'free_bytes':2097152,'monotonic':time.monotonic()}),flush=True)\ntime.sleep(30)\n"
    monkeypatch.setattr(reasoning_probe, '_read_bytes', lambda _: code)
    original_popen = subprocess.Popen
    children = []
    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(image_build.subprocess, 'Popen', popen)
    def failed_start(self):
        raise RuntimeError('reader cannot start')
    monkeypatch.setattr(image_build.threading.Thread, 'start', failed_start)
    with (tmp_path / 'output').open('wb') as log:
        result = image_build._stream_build([sys.executable, '-c', 'import time; time.sleep(30)'],
            source_bytes=b'', log=log, timeout=5,
            guard={'context': str(tmp_path), 'min_host_available_mib': 1, 'min_context_free_mib': 1})
    assert result['returncode'] == -1 and result['failure_type'] == 'RuntimeError'
    assert result['cleanup_errors'] == [{'stage': 'reader_start', 'type': 'RuntimeError'}]
    assert 'daemon_state_unverified' in result['cancellation']
    assert len(children) == 2 and all(child.poll() is not None for child in children)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux-only guard')
def test_guarded_selector_cleanup_failure_does_not_skip_observer(tmp_path, monkeypatch):
    from anvil_serving import reasoning_probe
    code = b"import json,time\nprint(json.dumps({'available_bytes':2097152,'free_bytes':2097152,'monotonic':time.monotonic()}),flush=True)\ntime.sleep(30)\n"
    monkeypatch.setattr(reasoning_probe, '_read_bytes', lambda _: code)
    original_popen = subprocess.Popen
    children = []
    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(image_build.subprocess, 'Popen', popen)
    native_selector = image_build.selectors.DefaultSelector
    class BrokenCloseSelector:
        def __init__(self): self.selector = native_selector()
        def register(self, *args): return self.selector.register(*args)
        def select(self, timeout): return self.selector.select(timeout)
        def close(self):
            self.selector.close()
            raise OSError('selector cleanup error')
    monkeypatch.setattr(image_build.selectors, 'DefaultSelector', BrokenCloseSelector)
    with (tmp_path / 'output').open('wb') as log:
        result = image_build._stream_build([sys.executable, '-c', 'pass'], source_bytes=b'', log=log, timeout=5,
            guard={'context': str(tmp_path), 'min_host_available_mib': 1, 'min_context_free_mib': 1})
    assert result['returncode'] == -1 and result['cleanup'] == 'unknown'
    assert result['cleanup_errors'] == [{'stage': 'selector_close', 'type': 'OSError'}]
    assert 'daemon_state_unverified' in result['cancellation']
    assert len(children) == 2 and all(child.poll() is not None for child in children)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux-only guard')
def test_guarded_owner_sigterm_reaps_owned_children(tmp_path):
    import json
    import os
    import signal
    import time
    receipt = tmp_path / 'pids.json'
    output = tmp_path / 'result.json'
    log = tmp_path / 'build.log'
    owner_source = '''
import json, subprocess, sys
from pathlib import Path
from anvil_serving import image_build, reasoning_probe
code = b"import json,time\\nprint(json.dumps({'available_bytes':2097152,'free_bytes':2097152,'monotonic':time.monotonic()}),flush=True)\\ntime.sleep(30)\\n"
reasoning_probe._read_bytes = lambda _: code
real_popen = subprocess.Popen
children = []
def popen(*a, **kw):
    child = real_popen(*a, **kw)
    children.append(child.pid)
    Path(%r).write_text(json.dumps(children))
    return child
image_build.subprocess.Popen = popen
with open(%r, 'wb') as log:
    result = image_build._stream_build([sys.executable, '-c', 'import time; time.sleep(30)'], source_bytes=b'', log=log, timeout=15,
        guard={'context':%r, 'min_host_available_mib':1, 'min_context_free_mib':1})
Path(%r).write_text(json.dumps(result))
''' % (str(receipt), str(log), str(tmp_path), str(output))
    owner = subprocess.Popen([sys.executable, '-B', '-c', owner_source],
                             cwd=str(Path(__file__).resolve().parents[1]),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        pids = []
        while time.monotonic() < deadline:
            try:
                pids = json.loads(receipt.read_text())
            except (OSError, ValueError):
                pass
            if len(pids) == 2:
                break
            if owner.poll() is not None:
                break
            time.sleep(0.02)
        assert len(pids) == 2
        os.kill(owner.pid, signal.SIGTERM)
        assert owner.wait(timeout=8) == 0
        result = json.loads(output.read_text())
        assert result['returncode'] == -1 and 'SIGTERM' in result['error']
        assert 'daemon_state_unverified' in result['cancellation']
        assert all(not Path('/proc', str(pid)).exists() for pid in pids)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux-only guard')
@pytest.mark.parametrize('signal_on_child', [1, 2])
@pytest.mark.parametrize('signal_name', ['SIGTERM', 'SIGINT'])
def test_signal_during_spawn_keeps_child_custody(tmp_path, monkeypatch, signal_on_child, signal_name):
    import os
    import signal
    from anvil_serving import reasoning_probe
    code = b"import json,time\nprint(json.dumps({'available_bytes':2097152,'free_bytes':2097152,'monotonic':time.monotonic()}),flush=True)\ntime.sleep(30)\n"
    monkeypatch.setattr(reasoning_probe, '_read_bytes', lambda _: code)
    original_popen = subprocess.Popen
    children = []
    sent_signal = getattr(signal, signal_name)
    previous = {kind: signal.getsignal(kind) for kind in (signal.SIGTERM, signal.SIGINT)}
    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        if len(children) == signal_on_child:
            os.kill(os.getpid(), sent_signal)
        return child
    monkeypatch.setattr(image_build.subprocess, 'Popen', popen)
    with (tmp_path / 'output').open('wb') as log:
        result = image_build._stream_build([sys.executable, '-c', 'import time; time.sleep(30)'],
            source_bytes=b'', log=log, timeout=5,
            guard={'context': str(tmp_path), 'min_host_available_mib': 1, 'min_context_free_mib': 1})
    assert result['returncode'] == -1 and signal_name in result['error']
    assert len(children) == signal_on_child and all(child.poll() is not None for child in children)
    assert all(signal.getsignal(kind) is handler for kind, handler in previous.items())
    if signal_on_child == 2:
        assert 'daemon_state_unverified' in result['cancellation']


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux-only guard')
def test_reader_join_failure_is_secondary_and_observer_still_stops(tmp_path, monkeypatch):
    from anvil_serving import reasoning_probe
    code = b"import json,time\nprint(json.dumps({'available_bytes':2097152,'free_bytes':2097152,'monotonic':time.monotonic()}),flush=True)\ntime.sleep(30)\n"
    monkeypatch.setattr(reasoning_probe, '_read_bytes', lambda _: code)
    original_popen = subprocess.Popen
    children = []
    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(image_build.subprocess, 'Popen', popen)
    def bad_join(self, timeout=None): raise RuntimeError('join failed')
    monkeypatch.setattr(image_build.threading.Thread, 'join', bad_join)
    with (tmp_path / 'output').open('wb') as log:
        result = image_build._stream_build([sys.executable, '-c', 'pass'], source_bytes=b'', log=log, timeout=5,
            guard={'context': str(tmp_path), 'min_host_available_mib': 1, 'min_context_free_mib': 1})
    assert result['returncode'] == -1 and result['failure_type'] == 'RuntimeError'
    assert {'stage': 'reader_join', 'type': 'RuntimeError'} in result['cleanup_errors']
    assert 'daemon_state_unverified' in result['cancellation']
    assert len(children) == 2 and all(child.poll() is not None for child in children)
