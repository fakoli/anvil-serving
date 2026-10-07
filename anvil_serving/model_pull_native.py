"""Pinned native HF pulls using an operator-installed executable and stdlib gates.

The Hub API supplies the exact selected manifest. The independent verifier reads
snapshot bytes and checks LFS SHA-256 or Git blob SHA-1, rather than trusting the
downloader's exit code or cache filenames. No cache artifacts are removed.
"""
from __future__ import annotations

from contextlib import contextmanager
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import model_cache_native


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_METADATA_BYTES = 16 * 1024**2
_MAX_FILES = 20_000
_TOKEN_VARIABLES = (
    "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN",
    "HUGGINGFACE_TOKEN", "HF_API_TOKEN",
)


class NativePullError(ValueError):
    """Native artifact acquisition cannot satisfy its explicit safety contract."""


def _safe_ancestry(path: Path) -> None:
    try:
        model_cache_native._no_symlink_ancestors(path, "native pull path")
    except model_cache_native.NativeCacheError as exc:
        raise NativePullError(str(exc)) from None
    current = path
    while True:
        if current.exists() and not current.is_dir():
            raise NativePullError("native pull directory ancestry contains a non-directory")
        if current.parent == current:
            return
        current = current.parent


def _cache_path(cache_dir: str) -> Path:
    if not isinstance(cache_dir, str) or not cache_dir.strip() or "\x00" in cache_dir:
        raise NativePullError("native pull requires an explicit --cache-dir")
    cache = Path(os.path.abspath(os.path.expanduser(cache_dir)))
    _safe_ancestry(cache)
    if (cache / "hub").exists():
        if any(cache.glob("models--*")):
            raise NativePullError("native cache has both hub/ and direct models-- entries")
        cache = cache / "hub"
        _safe_ancestry(cache)
    return cache


def _executable(value: str | None, environ: dict[str, str]) -> str:
    requested = value or "hf"
    if not isinstance(requested, str) or not requested or requested.startswith("-") or any(
        character in requested for character in "\x00\r\n"
    ):
        raise NativePullError("--hf-executable must name one installed executable")
    executable = shutil.which(os.path.expanduser(requested), path=environ.get("PATH", ""))
    if not executable:
        raise NativePullError("hf executable is unavailable; declare an installed --hf-executable")
    executable = str(Path(executable).resolve())
    if not Path(executable).is_file():
        raise NativePullError("hf executable must be a regular executable file")
    return executable


def _executable_identity(executable: str) -> dict:
    path = Path(executable)
    info = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    def identity(item):
        return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(info) != identity(after):
        raise NativePullError("hf executable changed while recording identity")
    return {"sha256": digest.hexdigest(), "device": info.st_dev,
            "inode": info.st_ino, "size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns}


def _downloader_version(executable: str, environ: dict) -> str:
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run([executable, "--version"], env=environ,
                                    stdout=output, stderr=output, timeout=10, check=False)
            output.seek(0)
            raw = output.read(4097)
    except (OSError, subprocess.TimeoutExpired):
        raise NativePullError("could not identify the declared hf executable") from None
    value = raw.decode("utf-8", errors="replace").strip()
    if result.returncode or len(raw) > 4096 or not re.fullmatch(r"(?:hf(?: CLI)?[ ,:]*(?:version[ :]+)?)?[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]*", value, re.IGNORECASE):
        raise NativePullError("declared executable did not report a bounded hf version")
    return value


def _filename(value: object) -> str:
    if (
        not isinstance(value, str) or not value or len(value) > 4096
        or "\\" in value or any(character in value for character in "\x00\r\n")
        or value.startswith("/") or "//" in value
        or any(part in {".", "..", ""} for part in value.split("/"))
        or PurePosixPath(value).as_posix() != value
    ):
        raise NativePullError("remote inventory contains an unsafe filename")
    return value


def _inventory(repo_id, revision, include, exclude, token, opener):
    endpoint = "https://huggingface.co/api/models/%s/revision/%s?blobs=true" % (
        urllib.parse.quote(repo_id, safe="/"), revision,
    )
    headers = {"Accept": "application/json", "User-Agent": "anvil-serving-native-model-pull"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with opener(urllib.request.Request(endpoint, headers=headers), timeout=30) as response:
            raw = response.read(_MAX_METADATA_BYTES + 1)
        if len(raw) > _MAX_METADATA_BYTES:
            raise NativePullError("remote inventory exceeds metadata size limit")
        data = json.loads(raw)
    except NativePullError:
        raise
    except urllib.error.HTTPError as exc:
        raise NativePullError("Hugging Face inventory request failed (HTTP %s)" % exc.code) from None
    except Exception as exc:
        # Transport exceptions can include credential-bearing request details.
        raise NativePullError("Hugging Face inventory request failed (%s)" % type(exc).__name__) from None
    if not isinstance(data, dict) or data.get("sha") != revision:
        raise NativePullError("remote inventory did not resolve the exact pinned revision")
    siblings = data.get("siblings")
    if not isinstance(siblings, list) or len(siblings) > _MAX_FILES:
        raise NativePullError("remote inventory is absent or exceeds the selected-file limit")
    selected = []
    names = set()
    for sibling in siblings:
        if not isinstance(sibling, dict):
            raise NativePullError("remote inventory contains a malformed file entry")
        filename = _filename(sibling.get("rfilename"))
        if filename in names:
            raise NativePullError("remote inventory contains duplicate filenames")
        names.add(filename)
        if include and not fnmatch.fnmatchcase(filename, include):
            continue
        if exclude and fnmatch.fnmatchcase(filename, exclude):
            continue
        size = sibling.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise NativePullError("selected remote file is missing its exact size: %s" % filename)
        lfs = sibling.get("lfs")
        if lfs is not None:
            if (
                not isinstance(lfs, dict) or not isinstance(lfs.get("sha256"), str)
                or not _SHA256.fullmatch(lfs["sha256"])
                or type(lfs.get("size")) is not int or lfs["size"] != size
            ):
                raise NativePullError("selected LFS file lacks a consistent public SHA-256: %s" % filename)
            algorithm, digest = "sha256", lfs["sha256"]
        else:
            digest = sibling.get("blobId")
            if not isinstance(digest, str) or not _COMMIT.fullmatch(digest):
                raise NativePullError("selected Git file lacks its public blob hash: %s" % filename)
            algorithm = "git-sha1"
        selected.append({"path": filename, "size_bytes": size,
                         "hash_algorithm": algorithm, "expected_hash": digest})
    if not selected:
        raise NativePullError("remote inventory contains no selected files")
    selected.sort(key=lambda item: item["path"])
    return {"source": endpoint, "revision": revision, "files": selected,
            "selected_file_count": len(selected),
            "selected_bytes": sum(item["size_bytes"] for item in selected)}


def _regular(path: Path):
    _safe_ancestry(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise NativePullError("selected cache blob is not a regular file")
    return info


def _hash(path: Path, file: dict) -> str:
    info = _regular(path)
    if info is None or info.st_size != file["size_bytes"]:
        raise NativePullError("selected file size mismatch: %s" % file["path"])
    digest = hashlib.sha256() if file["hash_algorithm"] == "sha256" else hashlib.sha1()
    if file["hash_algorithm"] == "git-sha1":
        digest.update(b"blob " + str(info.st_size).encode("ascii") + b"\0")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise NativePullError("selected file changed before hashing")
        while chunk := handle.read(4 * 1024**2):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise NativePullError("selected file changed while hashing")
    observed = digest.hexdigest()
    if observed != file["expected_hash"]:
        raise NativePullError("selected file hash mismatch: %s; cache bytes retained" % file["path"])
    return observed


def _snapshot_file(snapshot: Path, repo: Path, file: dict) -> Path | None:
    path = snapshot / file["path"]
    _safe_ancestry(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        name, _size, issue = model_cache_native._snapshot_link(path, repo, repo / "blobs")
        if issue:
            raise NativePullError("unsafe selected snapshot link: %s (%s)" % (file["path"], issue))
        return repo / "blobs" / name
    if not stat.S_ISREG(info.st_mode):
        raise NativePullError("selected snapshot entry is not a file: %s" % file["path"])
    return path


def _preflight(cache, repo, snapshot, inventory, headroom, disk_usage):
    _safe_ancestry(repo)
    _safe_ancestry(repo / "blobs")
    _safe_ancestry(snapshot)
    _exact_snapshot_entries(snapshot, inventory)
    cached = 0
    missing_hashes = {}
    checked = set()
    for file in inventory["files"]:
        selected = _snapshot_file(snapshot, repo, file)
        blob = repo / "blobs" / file["expected_hash"]
        candidate = selected or (blob if _regular(blob) is not None else None)
        if candidate is not None:
            key = (str(candidate), file["hash_algorithm"], file["expected_hash"], file["size_bytes"])
            if key not in checked:
                _hash(candidate, file)
                checked.add(key)
            cached += file["size_bytes"]
        else:
            prior_size = missing_hashes.setdefault(file["expected_hash"], file["size_bytes"])
            if prior_size != file["size_bytes"]:
                raise NativePullError("remote inventory has inconsistent sizes for a shared blob")
    existing = cache
    while not existing.exists():
        existing = existing.parent
    free = int(disk_usage(existing).free)
    # Existing incomplete blobs stay in place for hf to resume. No speculative
    # partial-byte credit can understate disk admission if those bytes are bad.
    missing = sum(missing_hashes.values())
    required = missing + headroom
    if free < required:
        raise NativePullError("insufficient native-cache space: need %d bytes (%d missing + %d headroom), have %d free"
                              % (required, missing, headroom, free))
    return {"expected_bytes": inventory["selected_bytes"], "cached_selected_bytes": cached,
            "missing_bytes": missing, "free_bytes": free, "headroom_bytes": headroom,
            "required_free_bytes": required, "space_ok": True,
            "partial_credit_bytes": 0}


def _exact_snapshot_entries(snapshot: Path, inventory: dict) -> None:
    if not snapshot.exists():
        return
    expected = {file["path"] for file in inventory["files"]}
    actual = set()
    for directory, dirs, names in os.walk(snapshot, followlinks=False):
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise NativePullError("snapshot directory symlink is not allowed")
        for name in names:
            actual.add((Path(directory) / name).relative_to(snapshot).as_posix())
    if actual - expected:
        raise NativePullError("snapshot contains files outside the declared selection; use a separate cache for filtered pulls")


def _verify(repo, snapshot, inventory):
    _safe_ancestry(snapshot)
    if not snapshot.is_dir():
        raise NativePullError("download returned success but the pinned snapshot is absent")
    _exact_snapshot_entries(snapshot, inventory)
    files = []
    checked = {}
    for file in inventory["files"]:
        path = _snapshot_file(snapshot, repo, file)
        if path is None:
            raise NativePullError("selected snapshot file is missing: %s" % file["path"])
        if os.path.lexists(repo / "blobs" / (file["expected_hash"] + ".incomplete")):
            raise NativePullError("selected file still has an incomplete cache artifact: %s" % file["path"])
        key = (str(path), file["hash_algorithm"], file["expected_hash"], file["size_bytes"])
        if key not in checked:
            checked[key] = _hash(path, file)
        files.append({**file, "observed_hash": checked[key], "verified": True})
    return {"status": "verified", "verified_file_count": len(files),
            "verified_bytes": inventory["selected_bytes"], "files": files,
            "completeness": "all_selected_remote_files"}


@contextmanager
def _writer(cache: Path, encoded_repo: str):
    lock_dir = cache / ".locks" / "anvil-serving"
    _safe_ancestry(lock_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    # Serialize disk admission and acquisition across all repos in this cache.
    lock = lock_dir / "native-pull.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise NativePullError("native pull lock is not a regular file")
    with os.fdopen(descriptor, "a+b") as handle:
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise NativePullError("native model pull already in progress for this repo/cache") from None
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise NativePullError("native model pull already in progress for this repo/cache") from None
            acquired = True
            yield handle.fileno()
        finally:
            if acquired:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    # Close releases our descriptor. An inherited child
                    # descriptor keeps the lock if the parent exits abruptly.
                    pass


def _receipt_path(cache, encoded_repo, revision, evidence_out):
    if evidence_out:
        target = Path(os.path.abspath(os.path.expanduser(evidence_out)))
        if target == cache or cache in target.parents:
            raise NativePullError("--evidence-out must be outside the hub cache")
        if os.path.lexists(target):
            raise NativePullError("--evidence-out must be a new file; prior evidence is retained")
    else:
        cache_id = hashlib.sha256(str(cache).encode()).hexdigest()[:16]
        target = (cache.parent / ".anvil-serving" / "model-pulls" / cache_id / encoded_repo
                  / (revision + "-" + uuid.uuid4().hex + ".json"))
    _safe_ancestry(target.parent)
    return target


def _reserve_receipt(target):
    _safe_ancestry(target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Receipt writes and truncation use encoded-byte lengths. On Windows,
        # text-mode newline translation would expand writes and truncate JSON.
        return os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL
                       | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        raise NativePullError("--evidence-out must be a new file; prior evidence is retained") from None


def _write_receipt(target, result, descriptor):
    # Write only our exclusively reserved inode, never replace another file.
    if not os.path.samestat(os.fstat(descriptor), target.lstat()):
        raise NativePullError("receipt path changed; no other evidence was overwritten")
    raw = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
    os.lseek(descriptor, 0, os.SEEK_SET)
    position = 0
    while position < len(raw):
        position += os.write(descriptor, raw[position:])
    os.ftruncate(descriptor, len(raw))
    os.fsync(descriptor)


def _diagnostic(value: str, secrets: list[str]) -> str:
    text = value[-4096:]
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"https?://\S+", "[URL omitted]", text)
    text = re.sub(r"(?i)(?:bearer\s+|(?:token|api[_-]?key|password)\s*[:=]\s*)\S+", "[credential omitted]", text)
    return text[-2048:]


def pull(repo_id, revision, cache_dir, *, hf_executable=None, include=None, exclude=None,
         token=None, no_token=False, headroom_bytes=5 * 1024**3, evidence_out=None,
         dry_run=False, _environ=None, _open=None, _run=None, _disk_usage=None):
    """Return a machine-readable preview or retained, independently verified pull.

    Apply callers must supply the CLI's authorization gate. The native module
    neither changes model-serving state nor installs the external downloader.
    """
    if not isinstance(repo_id, str) or not model_cache_native._valid_repo_id(repo_id):
        raise NativePullError("repository must be an exact OWNER/REPO id")
    if not isinstance(revision, str) or not _COMMIT.fullmatch(revision):
        raise NativePullError("native revision must be exactly 40 lowercase hexadecimal characters")
    if isinstance(headroom_bytes, bool) or not isinstance(headroom_bytes, int) or headroom_bytes < 0:
        raise NativePullError("native headroom must be a nonnegative integer byte count")
    for value in (include, exclude):
        if value is not None and (not isinstance(value, str) or not value or value.startswith("-")
                                  or any(character in value for character in "\x00\r\n")):
            raise NativePullError("native file filters must be one non-option glob")
    environ = dict(os.environ if _environ is None else _environ)
    cache = _cache_path(cache_dir)
    executable = _executable(hf_executable, environ)
    encoded = "models--" + repo_id.replace("/", "--")
    repo = cache / encoded
    snapshot = repo / "snapshots" / revision
    receipt = _receipt_path(cache, encoded, revision, evidence_out)
    argv = [executable, "download", repo_id, "--revision", revision, "--cache-dir", str(cache)]
    if include:
        argv += ["--include", include]
    if exclude:
        argv += ["--exclude", exclude]
    inventory = _inventory(repo_id, revision, include, exclude,
                           None if no_token or dry_run else token, _open or urllib.request.urlopen)
    usage = _disk_usage or shutil.disk_usage
    preflight = _preflight(cache, repo, snapshot, inventory, headroom_bytes, usage)
    result = {"schema_version": "native-model-pull/v1", "backend": "native",
              "status": "preview", "exit_code": 0, "repo_id": repo_id, "revision": revision,
              "cache_dir": str(cache), "snapshot_path": str(snapshot), "hf_argv": argv,
              "downloader_identity": _executable_identity(executable),
              "inventory": inventory, "preflight": preflight,
              "token_mode": "disabled" if no_token else "declared_secret_reference",
              "verification": {"status": "deferred"}, "retained_result": None,
              "recovery": "rerun the same pinned command; existing complete and partial downloads remain",
              "rollback": "none automatic; no cache artifacts are pruned or deleted"}
    if dry_run:
        return result
    with _writer(cache, encoded) as lock_descriptor:
        # Another invocation may have completed between preview/admission and
        # acquiring our one repo/cache writer. Recheck under the held lock.
        result["preflight"] = _preflight(cache, repo, snapshot, inventory, headroom_bytes, usage)
        result.update(status="downloading", retained_result=str(receipt))
        secrets = [environ.get(name, "") for name in _TOKEN_VARIABLES] + [token or ""]
        for name in _TOKEN_VARIABLES:
            environ.pop(name, None)
        if token and not no_token:
            environ["HF_TOKEN"] = token
        environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1" if no_token or not token else "0"
        environ["HF_ENDPOINT"] = "https://huggingface.co"
        # Native admission covers only the declared cache disk. Disable Xet's
        # separate chunk cache so the downloader uses resumable HTTP blobs.
        environ["HF_HUB_DISABLE_XET"] = "1"
        environ["HF_HUB_DISABLE_UPDATE_CHECK"] = "1"
        result["downloader_version"] = _downloader_version(executable, environ)
        if result["downloader_identity"] != _executable_identity(executable):
            raise NativePullError("hf executable changed before download")
        with os.fdopen(_reserve_receipt(receipt), "r+b") as receipt_handle:
            receipt_descriptor = receipt_handle.fileno()
            _write_receipt(receipt, result, receipt_descriptor)
            with tempfile.TemporaryFile() as errors:
                kwargs = {"env": environ, "stdout": subprocess.PIPE, "stderr": errors,
                          "text": True, "encoding": "utf-8", "errors": "replace", "check": False}
                if os.name != "nt":
                    kwargs["pass_fds"] = (lock_descriptor,)
                try:
                    completed = (_run or subprocess.run)(argv, **kwargs)
                    rc = int(completed.returncode)
                except OSError as exc:
                    result.update(status="download_failed", exit_code=127,
                                  error="could not execute installed hf (%s)" % type(exc).__name__)
                else:
                    if rc:
                        errors.seek(0, os.SEEK_END)
                        errors.seek(max(0, errors.tell() - 4096))
                        detail = errors.read().decode("utf-8", errors="replace")
                        detail += str(getattr(completed, "stderr", "") or "")[-4096:]
                        result.update(status="download_failed", exit_code=rc,
                                      error="hf download failed; cache bytes retained for resumption",
                                      downloader_error=_diagnostic(detail, secrets))
                    else:
                        try:
                            result["verification"] = _verify(repo, snapshot, inventory)
                            result["status"] = "verified"
                        except (NativePullError, OSError) as exc:
                            result.update(status="verification_failed", exit_code=5,
                                          error=str(exc) if isinstance(exc, NativePullError)
                                          else "could not read selected snapshot files")
                            result["verification"] = {"status": "failed"}
            _write_receipt(receipt, result, receipt_descriptor)
    return result
