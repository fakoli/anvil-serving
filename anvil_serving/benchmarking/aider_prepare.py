"""Pinned Aider preparation; no model traffic and no host execution of task code."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from .artifacts import atomic_write_json, path_is_within, real_path
from .jobs import BenchmarkJobError, canonical_json_bytes
from . import harnesses
from .aider_adapter import SOURCE_HASHES as ADAPTER_SOURCE_HASHES

AIDER_PYTHON_ENVIRONMENT_SCHEMA = "anvil-serving.aider-python-environment/v1"
AIDER_ENVIRONMENT_SCHEMA = "anvil-serving.aider-environment/v1"
AIDER_REVISION = "5dc9490bb35f9729ef2c95d00a19ccd30c26339c"
# The pinned __init__.py declares 0.86.3.dev. Normalize its implicit dev zero
# and attach the full commit as a local build identity, not an upstream release.
AIDER_SOURCE_VERSION = "0.86.3.dev0+g" + AIDER_REVISION
SCM_VERSION_VARIABLE = "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_AIDER_CHAT"
SCM_VERSION_ASSIGNMENT = SCM_VERSION_VARIABLE + "=" + AIDER_SOURCE_VERSION
SOURCE_VERSION_METADATA = {"version": AIDER_SOURCE_VERSION, "upstream_declared_version": "0.86.3.dev",
                           "revision": AIDER_REVISION, "source_file": "aider/__init__.py",
                           "mechanism": SCM_VERSION_VARIABLE, "upstream_release": False}
POLYGLOT_REVISION = "7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f"
SOURCE_HASHES = {
    **ADAPTER_SOURCE_HASHES,
    "aider/__init__.py": "2d49cdf776f9392fd7e30ad70cec22e81df1de84c388f840618a087775ebc831",
    "requirements.txt": "5b340fe0a8830196dbfc18098edd73c379eeae05f752ab47979e96cb542f095f",
    "benchmark/Dockerfile": "352813bb478c35b88981d03a23679b33f5d1a78871c53173a68b52422fe68e80",
}
LANGUAGES = ("cpp", "go", "java", "javascript", "python", "rust")
LINT_CONTRACT = {"command_prefix": ["python3", "-I", "-m", "flake8"], "version": "7.3.0"}
JEST_VERSION = "29.7.0"
JEST_DEPENDENCY_PIN = {"package": "jest", "version": JEST_VERSION,
                       "upstream_recipe": "unversioned jest", "task_requirement": "^29.7.0",
                       "reason": "preserve the pinned task's toThrowError oracle contract"}
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _adapter_identity():
    return {"sha256": hashlib.sha256(Path(__file__).with_name("aider_adapter.py").read_bytes()).hexdigest(),
            "containment_sha256": hashlib.sha256(Path(__file__).with_name("aider_containment.py").read_bytes()).hexdigest(),
            "lint": LINT_CONTRACT}


def _bounded_runner(argv, cwd, timeout):
    from ..transports import _run_bounded_process
    return _run_bounded_process(argv, cwd=cwd, timeout=timeout, max_output_bytes=8 * 1024**2,
                                env={"PATH": os.defpath, "HOME": cwd or "/tmp", "PIP_CONFIG_FILE": os.devnull})


def _copy_source(root, target, runner):
    listing = harnesses._run(runner, ("git", "ls-files", "-z"), cwd=str(root))
    if listing["returncode"] or listing["output_truncated"]:
        _fail("pinned Aider source inventory could not be read")
    names = set(filter(None, listing["stdout"].split("\0")))
    resolved_root = root.resolve()
    for name in sorted(names):
        source = root / name
        if Path(name).is_absolute() or ".." in Path(name).parts:
            _fail("unsupported Aider source link or path")
        try:
            resolved = source.resolve(strict=True)
            relative = resolved.relative_to(resolved_root).as_posix()
        except (OSError, ValueError, RuntimeError):
            _fail("unsupported Aider source link or path")
        if not resolved.is_file() or relative not in names:
            _fail("unsupported Aider source link or path")
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Materialize pinned in-tree links; build contexts remain link-free.
        shutil.copy2(resolved, destination)


def _cache_target(cache_root, key):
    target = Path(cache_root) / key
    if target.is_symlink() or not target.resolve().is_relative_to(Path(cache_root).resolve()):
        _fail("Aider environment escaped the cache")
    return target


def _build_files(context):
    files = {}
    for path in sorted(context.rglob("*")):
        if path.is_symlink():
            _fail("Aider build context contains a symlink")
        if path.is_file():
            files[path.relative_to(context).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _environment_digest(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.as_posix() == "environment.json":
            continue
        if path.is_symlink():
            # Venv interpreter links may name the base Python; other links must
            # remain in this environment (including the usual lib64 -> lib).
            interpreter = relative.parent == Path("bin") and re.fullmatch(r"python(?:3(?:\.12)?)?", relative.name)
            if not path.resolve(strict=True).is_relative_to(root.resolve()) and not interpreter:
                _fail("Aider Python environment contains an external link")
            digest.update(canonical_json_bytes([relative.as_posix(), "symlink", os.readlink(path)]))
        if path.is_file():
            digest.update(canonical_json_bytes([relative.as_posix(), "file", path.stat().st_mode & 0o777]))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        elif not path.is_dir():
            _fail("Aider Python environment contains a non-regular file")
    return digest.hexdigest()


def _python_identity():
    return {"schema": AIDER_PYTHON_ENVIRONMENT_SCHEMA, "source_version": SOURCE_VERSION_METADATA,
            "python_minor": "3.12", "platform": platform.system().lower(),
            "architecture": platform.machine().lower(),
            "sources": {"aider": {"source": "https://github.com/Aider-AI/aider", "revision": AIDER_REVISION}}}


def verify_aider_python_environment(environment, *, cache_root):
    """Bind the receipt to actual executable bytes, without running cached code."""
    if not isinstance(environment, dict):
        _fail("Aider Python environment receipt is absent")
    identity = _python_identity()
    key = "aider-python-environments/" + _digest(identity)
    packages = environment.get("resolved_packages")
    if (environment.get("identity") != identity or any(environment.get(k) != v for k, v in identity.items())
            or not isinstance(environment.get("python"), dict)
            or not re.fullmatch(r"3\.12\.[0-9]+", str(environment.get("python", {}).get("version", "")))
            or environment.get("schema") != AIDER_PYTHON_ENVIRONMENT_SCHEMA
            or environment.get("cache_key") != key or environment.get("executable") != "bin/python"
            or not isinstance(packages, list) or not packages or not all(isinstance(p, str) for p in packages)
            or environment.get("resolved_packages_sha256") != _digest(packages)
            or not re.fullmatch(r"[0-9a-f]{64}", str(environment.get("files_sha256", "")))):
        _fail("Aider Python environment receipt identity changed")
    root = Path(cache_root).resolve()
    target = root / key
    try:
        if any(p.is_symlink() for p in (target, target.parent)):
            _fail("Aider Python environment cache path is a symlink")
        lock_path = target / "environment.json"
        python = target / "bin/python"
        if (not target.is_dir() or lock_path.is_symlink() or not lock_path.is_file()
                or lock_path.stat().st_size > 1024**2 or not python.is_file()):
            _fail("Aider Python environment has no valid receipt or interpreter")
        lock = json.loads(lock_path.read_text())
        recorded = {k: v for k, v in environment.items() if k not in {"cache_key", "executable", "reused"}}
        if lock != recorded or environment["files_sha256"] != _environment_digest(target):
            _fail("Aider Python environment bytes or receipt changed")
    except (OSError, ValueError, RuntimeError) as exc:
        raise BenchmarkJobError("aider_preparation_mismatch", "Aider Python environment could not be verified") from exc
    return str(python)


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _fail(message, code="aider_preparation_mismatch"):
    raise BenchmarkJobError(code, message)


def _source(cache_root, assets, name):
    value = assets.get(name, {})
    revision = AIDER_REVISION if name == "aider" else POLYGLOT_REVISION
    source = ("https://github.com/Aider-AI/aider" if name == "aider" else
              "https://github.com/paraemsi/polyglot-benchmark")
    if value.get("revision") != revision or value.get("source") != source:
        _fail(f"{name} does not match the supported immutable source")
    path = Path(cache_root) / value.get("cache_key", "")
    if path.is_symlink() or not path_is_within(real_path(str(path)), real_path(cache_root)):
        _fail("Aider source escaped the cache")
    return path


def verify_aider_sources(cache_root, assets, *, runner=harnesses._default_runner):
    """Verify cached Git bytes again; a previous acquisition lock is insufficient."""
    roots = {}
    for name in ("aider", "polyglot-benchmark"):
        root = _source(cache_root, assets, name)
        head = harnesses._run(runner, ("git", "rev-parse", "HEAD"), cwd=str(root))
        status = harnesses._run(runner, ("git", "status", "--porcelain", "--untracked-files=all"), cwd=str(root))
        changes = [line for line in status["stdout"].splitlines()
                   if line != "?? .anvil-harness-lock.json"]
        if (head["returncode"] or status["returncode"] or changes or
                head["stdout"].strip() != assets[name]["revision"] or
                head["output_truncated"] or status["output_truncated"]):
            _fail(f"{name} cached source identity changed")
        roots[name] = root
    for relative, expected in SOURCE_HASHES.items():
        path = roots["aider"] / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            _fail(f"pinned Aider helper hash mismatch: {relative}")
    return roots


def _selection(polyglot):
    from .aider_adapter import task_snapshot
    selected = []
    for language in LANGUAGES:
        configs = sorted(polyglot.glob(f"{language}/exercises/practice/*/.meta/config.json"))
        if not configs:
            _fail(f"missing {language} oracle task")
        task = configs[0].parent.parent
        task_id = task.relative_to(polyglot).as_posix()
        files = task_snapshot(polyglot, task_id)
        config = json.loads(configs[0].read_text())
        groups = config.get("files", {})
        solutions, examples, tests = (groups.get(key, []) for key in ("solution", "example", "test"))
        solutions = [name for name in solutions if name not in {"Cargo.toml", "CMakeLists.txt"}]
        if not solutions or len(solutions) != len(examples) or not tests:
            _fail(f"oracle reference is incomplete: {task_id}")
        for name in (*solutions, *examples, *tests):
            if name not in files:
                _fail(f"oracle file is absent: {task_id}/{name}")
        selected.append({"task_id": task_id, "language": language, "files": files,
                         "solutions": solutions, "examples": examples, "tests": tests})
    return selected


def _copy_task(source, destination, row, *, reference=False):
    shutil.copytree(source / row["task_id"], destination)
    if reference:
        for solution, example in zip(row["solutions"], row["examples"]):
            shutil.copyfile(destination / example, destination / solution)


def _checked(runner, command, logs, *, cwd=None, timeout=1800):
    result = harnesses._run(runner, command, cwd=cwd, timeout=timeout)
    logs.append(result)
    if result["returncode"]:
        raise BenchmarkJobError("aider_preparation_failure", "managed Aider preparation command failed", {"step": result})
    return result["stdout"].strip()


def _python_environment(assets, cache_root, offline, max_bytes, runner, logs):
    identity = _python_identity()
    key = "aider-python-environments/" + _digest(identity)
    target = _cache_target(cache_root, key)
    lock_path = target / "environment.json"
    python = str(target / "bin/python")
    if target.exists():
        try:
            lock = json.loads(lock_path.read_text())
        except (OSError, ValueError):
            _fail("cached Aider Python environment has no complete receipt")
        python = verify_aider_python_environment(
            {**lock, "cache_key": key, "executable": "bin/python", "reused": True}, cache_root=cache_root)
        version = _checked(runner, (python, "-I", "-B", "-c", "import platform; print(platform.python_version())"), logs)
        inventory = harnesses._normalized_freeze(_checked(runner, (python, "-I", "-B", "-m", "pip", "freeze", "--all"), logs), cache_root=cache_root)
        if (lock.get("identity") != identity or lock.get("python", {}).get("version") != version or
                not version.startswith("3.12.") or lock.get("resolved_packages") != inventory or
                lock.get("resolved_packages_sha256") != _digest(inventory) or
                lock.get("files_sha256") != _environment_digest(target)):
            _fail("cached Aider Python identity or inventory changed")
        return {**lock, "cache_key": key, "executable": "bin/python", "reused": True}
    if offline:
        _fail("Aider Python environment is absent offline", "harness_assets_offline")
    executable = sys.executable if sys.version_info[:2] == (3, 12) else shutil.which("python3.12")
    if not executable:
        _fail("Aider preparation requires Python 3.12", "aider_python_absent")
    target.mkdir(parents=True)
    try:
        _checked(runner, (executable, "-I", "-m", "venv", str(target)), logs)
        build_source = target / "source"
        _copy_source(_source(cache_root, assets, "aider"), build_source, runner)
        # Install a regular wheel, never an editable source with ambient imports.
        _checked(runner, ("env", SCM_VERSION_ASSIGNMENT, python, "-I", "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "--no-input",
                          str(build_source) + "[dev]"), logs, cwd=str(target))
        version = _checked(runner, (python, "-I", "-c", "import platform; print(platform.python_version())"), logs)
        inventory = harnesses._normalized_freeze(_checked(runner, (python, "-I", "-B", "-m", "pip", "freeze", "--all"), logs), cache_root=cache_root)
        installed_version = _checked(runner, (python, "-I", "-c",
            "from importlib.metadata import version; print(version('aider-chat'))"), logs)
        if not version.startswith("3.12.") or not inventory or installed_version != AIDER_SOURCE_VERSION:
            _fail("Aider Python or pinned package version did not verify")
        if harnesses._directory_bytes(str(target)) > max_bytes:
            _fail("Aider Python environment exceeded the disk bound")
        lock = {**identity, "identity": identity, "python": {"version": version},
                "resolved_packages": inventory, "resolved_packages_sha256": _digest(inventory),
                "files_sha256": _environment_digest(target)}
        atomic_write_json(str(lock_path), lock)
        return {**lock, "cache_key": key, "executable": "bin/python", "reused": False}
    except BaseException:
        shutil.rmtree(target)
        raise


# Executed only inside the preparation image. Runtime copies caches because the
# final image root is read-only and the caller runs as an unprivileged UID.
_ENTRYPOINT = '''import os, pathlib, shutil, subprocess, sys
root = pathlib.Path('/task/.anvil-runtime')
root.mkdir(exist_ok=True)
(root/'tmp').mkdir(exist_ok=True)
for name in ('cargo', 'gradle', 'go'):
    src, dst = pathlib.Path('/opt/prepared-cache') / name, root / name
    if not dst.exists():
        shutil.copytree(src, dst)
os.environ.update(CARGO_HOME=str(root/'cargo'), GRADLE_USER_HOME=str(root/'gradle'), GOPATH=str(root/'go'), GOCACHE='/tmp/go-cache', TMPDIR=str(root/'tmp'), HOME='/tmp')
try:
    result = subprocess.run(sys.argv[1:], check=False)
finally:
    # Upstream npm helper creates image-relative links; remove them after tests
    # so the next strict task-mount check never admits an escaping host link.
    for path in pathlib.Path('/task').rglob('*'):
        if path.is_symlink():
            path.unlink()
sys.exit(result.returncode)
'''
_WARM = '''import json, os, pathlib, re, shutil, subprocess
rows = json.loads(pathlib.Path('/aider/anvil-selection.json').read_text())
commands = {'.py':['pytest'], '.rs':['cargo','test','--','--include-ignored'], '.go':['go','test','./...'], '.js':['/aider/benchmark/npm-test.sh'], '.cpp':['/aider/benchmark/cpp-test.sh'], '.java':['./gradlew','test']}
for row in rows:
    task = pathlib.Path('/aider/anvil-tasks') / row['task_id']
    for solution, example in zip(row['solutions'], row['examples']):
        shutil.copyfile(task/example, task/solution)
    extensions = {pathlib.Path(f).suffix for f in row['tests']}
    command = next(value for key,value in commands.items() if key in extensions)
    for name in row['tests']:
        if name.endswith('.java'):
            file=task/name
            file.write_text(re.sub(r'@Disabled\\([^)]*\\)\\s*\\n', '', file.read_text()))
    subprocess.run(command, cwd=task, check=True, timeout=600)
'''


def _build_image(roots, rows, target, container, max_bytes, runner, logs):
    from ..image_build import build_plan

    context = target / "build"
    _copy_source(roots["aider"], context, runner)
    for row in rows:
        _copy_task(roots["polyglot-benchmark"], context / "anvil-tasks" / row["task_id"], row)
    atomic_write_json(str(context / "anvil-selection.json"), rows)
    atomic_write_json(str(context / "anvil-source-version.json"), SOURCE_VERSION_METADATA)
    atomic_write_json(str(context / "anvil-adapter-identity.json"), _adapter_identity())
    (context / "anvil-entrypoint.py").write_text(_ENTRYPOINT)
    (context / "anvil-warm.py").write_text(_WARM)
    original = (roots["aider"] / "benchmark/Dockerfile").read_text()
    if original.splitlines()[0] != "FROM buildpack-deps:jammy":
        _fail("unexpected pinned Aider base recipe")
    _checked(runner, (container, "pull", "buildpack-deps:jammy"), logs)
    base = json.loads(_checked(runner, (container, "image", "inspect", "buildpack-deps:jammy"), logs))[0]
    digests = base.get("RepoDigests", [])
    if not digests or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", digests[0]):
        _fail("Aider base image did not resolve to an immutable digest")
    install = "RUN uv pip install --system --no-cache-dir -e /aider[dev]"
    if original.count(install) != 1:
        _fail("unexpected pinned Aider installation command")
    recipe = original.replace("FROM buildpack-deps:jammy", "FROM " + digests[0], 1)
    recipe = recipe.replace(install, "RUN " + SCM_VERSION_ASSIGNMENT + install[3:], 1)
    jest_line = "    jest " + chr(92) + "\n"
    if recipe.count(jest_line) != 1:
        _fail("unexpected pinned Aider Jest installation command")
    recipe = recipe.replace(jest_line, "    jest@" + JEST_VERSION + " " + chr(92) + "\n", 1)
    atomic_write_json(str(context / "anvil-dependency-pins.json"), [JEST_DEPENDENCY_PIN])
    recipe += '''\nRUN mv /root/.cargo /opt/cargo && mv /root/.rustup /opt/rustup && chmod -R a+rX /opt/cargo /opt/rustup
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/prepared-cache/cargo GRADLE_USER_HOME=/opt/prepared-cache/gradle GOPATH=/opt/prepared-cache/go
ENV PATH=/opt/cargo/bin:${PATH}
RUN mkdir -p /opt/prepared-cache/cargo /opt/prepared-cache/gradle /opt/prepared-cache/go && python3 /aider/anvil-warm.py && chmod -R a+rX /opt/prepared-cache
ENTRYPOINT ["python3", "/aider/anvil-entrypoint.py"]
WORKDIR /task
'''
    (context / "Dockerfile").write_text(recipe)
    files = _build_files(context)
    if harnesses._directory_bytes(str(context)) > max_bytes:
        _fail("Aider build context exceeded the disk bound")
    build_config = target / "image-build.toml"
    build_config.write_text(
        'schema = "anvil-serving.image-builds/v1"\n[builds.aider]\n'
        'context = "build"\ndockerfile = "Dockerfile"\n'
        f'image = "anvil-benchmark-aider:{target.name}"\n'
        'cpus = 4\nmemory_mib = 8192\ntimeout_seconds = 3600\n'
        f'platform = "linux/{"arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"}"\n'
    )
    plan = build_plan("aider", build_config)
    help_text = _checked(runner, (container, "buildx", "build", "--help"), logs)
    if "--resource" not in help_text:
        _fail("Docker Buildx lacks bounded build resources; no build started")
    image_file = target / "image-id"
    command = (container, *plan["argv"][1:-1], "--pull=false", "--iidfile", str(image_file), str(context))
    _checked(runner, command, logs, timeout=plan["timeout_seconds"])
    image_id = image_file.read_text().strip()
    info = json.loads(_checked(runner, (container, "image", "inspect", image_id), logs))[0]
    if not IMAGE_ID.fullmatch(image_id) or info.get("Id") != image_id or not 0 < info.get("Size", 0) <= max_bytes:
        _fail("Aider image identity or disk bound failed")
    return image_id, {"base_digest": digests[0], "files": files, "sha256": _digest(files),
                      "image_size_bytes": info["Size"], "rebuild_bit_reproducible": False,
                      "dependency_pins": [JEST_DEPENDENCY_PIN],
                      "build_limits": {key: plan[key] for key in ("cpus", "memory_mib", "timeout_seconds", "platform")}}


def _oracle(roots, rows, run_path, ownership_id, run_id, image_id, container, runner, logs):
    from .aider_adapter import load_test_callback, contained_test_callback
    from .aider_containment import run_contained_test
    results = []
    receipt = Path(run_path) / "aider/preparation/oracle.json"
    original = roots["polyglot-benchmark"]
    callback = load_test_callback(roots["aider"])
    failures = []
    for row in rows:
        result = {"task_id": row["task_id"], "language": row["language"]}
        for mode in ("starter", "reference"):
            task = Path(run_path) / "aider/preparation" / mode / row["task_id"]
            _copy_task(original, task, row, reference=mode == "reference")
            observed = []
            def contained(command, **kwargs):
                value = run_contained_test(["python3", "/aider/anvil-entrypoint.py", *command], task_dir=task, run_path=run_path,
                    ownership_id=ownership_id, run_id=run_id, image_id=image_id,
                    timeout=kwargs["timeout"], container_binary=container, runner=runner)
                observed.append(value.returncode)
                result["command"] = list(command)
                output, truncated = harnesses._bounded_text(value.stdout)
                result[mode + "_output"] = output
                result[mode + "_output_truncated"] = truncated
                return value
            try:
                contained_test_callback(callback, contained)(original, task, task / "oracle-history.md", row["tests"])
            except subprocess.TimeoutExpired:
                result[mode + "_timeout"] = True
            result[mode + "_returncode"] = observed[0] if len(observed) == 1 else None
            atomic_write_json(str(receipt), {"status": "incomplete", "image_id": image_id, "results": [*results, result]})
            code = result[mode + "_returncode"]
            if code is None or (mode == "reference" and code != 0) or (mode == "starter" and not 0 < code < 125):
                failures.append(f"{row['language']} {mode}")
        results.append(result)
    if failures:
        _fail(", ".join(failures) + " oracle did not verify", "aider_oracle_failed")
    value = {"status": "passed", "image_id": image_id, "results": results}
    atomic_write_json(str(receipt), value)
    return value


def _image_inventory(image_id, run_path, ownership_id, run_id, container, runner):
    from .aider_containment import run_contained_test
    task = Path(run_path) / "aider/preparation/inventory"
    task.mkdir(parents=True, exist_ok=True)
    lint = run_contained_test(["python3", "/aider/anvil-entrypoint.py",
                              *LINT_CONTRACT["command_prefix"], "--version"],
        task_dir=task, run_path=run_path, ownership_id=ownership_id, run_id=run_id,
        image_id=image_id, container_binary=container, runner=runner)
    lint_output, truncated = harnesses._bounded_text(lint.stdout)
    if lint.returncode or truncated or not lint_output.startswith(LINT_CONTRACT["version"] + " "):
        _fail("prepared Aider flake8 dependency did not verify")
    command = ["sh", "-c", "set -e; dpkg-query -W; python3 -I -m pip freeze --all; "
               "python3 -I -c \"from importlib.metadata import version; "
               f"assert version('aider-chat') == '{AIDER_SOURCE_VERSION}'; "
               "print(version('aider-chat'))\"; "
               "node --version; node -e \"const p=require('/npm-install/package-lock.json').packages; "
               f"if(p['node_modules/jest'].version!=='{JEST_VERSION}') throw Error('Jest version mismatch'); "
               "console.log(JSON.stringify(p))\"; "
               "go version; /opt/cargo/bin/rustc --version; java -version 2>&1"]
    result = run_contained_test(command, task_dir=task, run_path=run_path,
        ownership_id=ownership_id, run_id=run_id, image_id=image_id,
        container_binary=container, runner=runner)
    raw = result.stdout
    output = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    if result.returncode or len(output.encode()) > 1024**2 or not output.strip():
        _fail("Aider image package inventory could not be fully recorded")
    return "Aider contained lint: " + lint_output.strip() + "\n" + output


def validate_aider_environment(environment, *, cache_root, assets):
    """Pure receipt gate used before any Aider model invocation."""
    if not isinstance(environment, dict) or environment.get("schema") != AIDER_ENVIRONMENT_SCHEMA:
        _fail("Aider environment receipt is absent")
    if not IMAGE_ID.fullmatch(str(environment.get("image_id", ""))):
        _fail("Aider image must be immutable")
    if environment.get("source_hashes") != SOURCE_HASHES:
        _fail("Aider source guards are absent or changed")
    for name in ("aider", "polyglot-benchmark"):
        _source(cache_root, assets, name)
    rows = environment.get("selection", [])
    if (not isinstance(rows, list) or len(rows) != 6 or not all(isinstance(r, dict) for r in rows)
            or {r.get("language") for r in rows} != set(LANGUAGES)):
        _fail("six-language prepared selection is incomplete")
    from .aider_adapter import validate_task_id
    for row in rows:
        try:
            task_id = validate_task_id(row.get("task_id"))
        except ValueError as exc:
            raise BenchmarkJobError("aider_preparation_mismatch", "invalid prepared task identity") from exc
        if task_id.split("/")[0] != row["language"] or not row.get("files"):
            _fail("prepared task language or snapshot is invalid")
    if environment.get("selection_sha256") != _digest(rows):
        _fail("prepared task selection changed")
    oracle = environment.get("oracle", {})
    if not isinstance(oracle, dict):
        _fail("invalid oracle receipt")
    results = oracle.get("results", [])
    if (not isinstance(results, list) or not all(isinstance(r, dict) for r in results) or oracle.get("status") != "passed" or oracle.get("image_id") != environment["image_id"] or
            len(results) != 6 or {r.get("task_id") for r in results} != {r["task_id"] for r in rows}):
        _fail("six-language oracle is incomplete")
    for row in results:
        code = row.get("starter_returncode")
        if (type(code) is not int or not 0 < code < 125 or type(row.get("reference_returncode")) is not int or row.get("reference_returncode") != 0 or
                row.get("starter_timeout") or row.get("reference_timeout")):
            _fail("starter/reference oracle failed")
    if (not environment.get("package_inventory") or
            environment.get("package_inventory_sha256") != _digest(environment["package_inventory"])):
        _fail("Aider package inventory is incomplete")
    return dict(environment)


def prepare_aider_environment(*, assets, cache_root, offline, max_download_bytes,
                              run_path, ownership_id, run_id, runner=harnesses._default_runner):
    """Return Python and image receipts only after all six independent oracles."""
    if platform.system().lower() not in {"linux", "darwin"}:
        _fail("Aider execution requires a POSIX container worker")
    if runner is harnesses._default_runner:
        runner = _bounded_runner
    logs = []
    receipt_path = Path(run_path) / "aider/preparation/receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        roots = verify_aider_sources(cache_root, assets, runner=runner)
        rows = _selection(roots["polyglot-benchmark"])
        python = _python_environment(assets, cache_root, offline, max_download_bytes, runner, logs)
        identity = {"source_hashes": SOURCE_HASHES, "source_version": SOURCE_VERSION_METADATA,
                    "selection": rows, "adapter": _adapter_identity(),
                    "platform": platform.system().lower(), "architecture": platform.machine().lower(),
                    "preparer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        key = "aider-environments/" + _digest(identity)
        target = _cache_target(cache_root, key)
        container = harnesses.resolve_container_binary()
        if not container:
            _fail("Docker is required for Aider preparation", "container_capability_absent")
        lock_path = target / "environment.json"
        if target.exists():
            try:
                environment = json.loads(lock_path.read_text())
            except (OSError, ValueError):
                _fail("cached Aider image has no complete receipt")
            validate_aider_environment(environment, cache_root=cache_root, assets=assets)
            if environment.get("identity") != identity:
                _fail("cached Aider preparation identity changed")
            inputs = environment["build_inputs"]
            if (_build_files(target / "build") != inputs["files"] or
                    _digest(inputs["files"]) != inputs["sha256"]):
                _fail("cached Aider build inputs changed")
            info = json.loads(_checked(runner, (container, "image", "inspect", environment["image_id"]), logs))[0]
            if info.get("Id") != environment["image_id"] or info.get("Size") != environment["build_inputs"]["image_size_bytes"]:
                _fail("cached Aider image changed")
            inventory = _image_inventory(environment["image_id"], run_path, ownership_id, run_id, container, runner)
            if inventory != environment["package_inventory"] or _digest(inventory) != environment["package_inventory_sha256"]:
                _fail("cached Aider package inventory changed")
            environment["reused"] = True
        else:
            if offline:
                _fail("prepared Aider image is absent offline", "harness_assets_offline")
            target.mkdir(parents=True)
            image_id, inputs = _build_image(roots, rows, target, container, max_download_bytes, runner, logs)
            inventory = _image_inventory(image_id, run_path, ownership_id, run_id, container, runner)
            oracle = _oracle(roots, rows, run_path, ownership_id, run_id, image_id, container, runner, logs)
            environment = {"schema": AIDER_ENVIRONMENT_SCHEMA, "cache_key": key,
                           "identity": identity, "image_id": image_id, "source_hashes": SOURCE_HASHES,
                           "selection": rows, "selection_sha256": _digest(rows), "build_inputs": inputs,
                           "oracle": oracle, "package_inventory": inventory, "package_inventory_sha256": _digest(inventory), "reused": False}
            validate_aider_environment(environment, cache_root=cache_root, assets=assets)
            atomic_write_json(str(lock_path), environment)
        occupied = (harnesses._directory_bytes(str(target)) +
                    harnesses._directory_bytes(str(Path(cache_root) / python["cache_key"])) +
                    environment["build_inputs"]["image_size_bytes"])
        if occupied > max_download_bytes:
            _fail("Aider preparation exceeded the combined disk bound")
        atomic_write_json(str(receipt_path), {"status": "ready", "python_environment": python,
                                             "aider_environment": environment, "command_logs": logs})
        return python, environment, logs
    except BaseException as exc:
        atomic_write_json(str(receipt_path), {"status": "failed", "error": str(exc), "command_logs": logs})
        raise
