"""Fail-closed disposal of one dedicated local Docker Hugging Face cache volume."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import tomllib
import urllib.parse
from contextlib import contextmanager
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from . import docker_images, model_cache_native, model_pull_native


_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_VOLUME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MAX_ENTRIES = 20_000
_MAX_NATIVE_DESCRIPTORS = 1_000_000
_MAX_NATIVE_MAPPINGS = 1_000_000
_MAX_SECONDS = 20
_CONFIG_SUFFIXES = {".toml", ".json", ".yaml", ".yml"}
_PATH_FIELD = re.compile(r"(?:^|[_.-])(?:caches?|volumes?|mounts?|paths?|dirs?|roots?|sources?|homes?)(?:$|[_.-])")


class ModelCacheVolumeError(ValueError):
    """The dedicated-volume deletion cannot be proved safe."""


class _DockerTimeout(ModelCacheVolumeError):
    """The daemon may complete a timed-out request after the client exits."""


def _valid(repo_id, revision, volume):
    if not isinstance(repo_id, str) or not _REPO.fullmatch(repo_id) or "--" in repo_id:
        raise ModelCacheVolumeError("repository must be an exact OWNER/REPO id")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise ModelCacheVolumeError("revision must be an exact 40-character lowercase commit")
    if not isinstance(volume, str) or not _VOLUME.fullmatch(volume):
        raise ModelCacheVolumeError("volume must be one safe Docker volume name")


def _run(args, *, runner, timeout=120):
    try:
        result = runner(["docker", *args], capture_output=True, text=True,
                        errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise _DockerTimeout("docker command timed out") from exc
    except OSError as exc:
        raise ModelCacheVolumeError("docker command is unavailable: %s" % exc) from exc
    if result.returncode:
        raise ModelCacheVolumeError((result.stderr or result.stdout or "docker command failed").strip())
    return result


def _plain_dir(path, label):
    candidate = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    current = candidate
    while True:
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ModelCacheVolumeError("%s must not have a symlinked ancestor" % label)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ModelCacheVolumeError("cannot inspect %s" % label) from exc
        if current.parent == current:
            break
        current = current.parent
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise ModelCacheVolumeError("cannot inspect %s" % label) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ModelCacheVolumeError("%s must be a plain existing directory" % label)
    return candidate


def _config_home(path):
    home = _plain_dir(path, "operator config home")
    # An empty directory is not an operator home and cannot prove rollback state.
    topology = home / "operator-topology.toml"
    try:
        info = topology.lstat()
    except OSError as exc:
        raise ModelCacheVolumeError("operator config home lacks operator-topology.toml") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ModelCacheVolumeError("operator config home lacks a plain operator-topology.toml")
    return home


def _volume(volume, *, runner):
    try:
        raw = _run(["volume", "inspect", volume], runner=runner).stdout
        rows = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelCacheVolumeError("docker volume inspect returned invalid JSON") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ModelCacheVolumeError("docker volume inspect returned incomplete identity")
    row = rows[0]
    name, driver, mount, scope = row.get("Name"), row.get("Driver"), row.get("Mountpoint"), row.get("Scope")
    options, labels, created = row.get("Options"), row.get("Labels"), row.get("CreatedAt")
    if name != volume or driver != "local" or scope not in (None, "local") or not isinstance(mount, str) or not os.path.isabs(mount):
        raise ModelCacheVolumeError("volume is not a local Docker volume with an absolute mountpoint")
    if options not in (None, {}) or not isinstance(labels, (dict, type(None))) or not isinstance(created, str) or not created:
        raise ModelCacheVolumeError("volume has unsupported driver options or incomplete metadata")
    mountpoint = _plain_dir(mount, "Docker volume mountpoint")
    if str(mountpoint) != mount or os.path.realpath(mount) != mount or not os.access(mountpoint, os.R_OK | os.X_OK):
        raise ModelCacheVolumeError("Docker volume mountpoint must be a readable canonical local path")
    info = mountpoint.stat()
    identity = {
        "name": name, "driver": driver, "created_at": created, "mountpoint": mount,
        "options": options or {}, "labels": labels or {}, "filesystem": {"dev": info.st_dev, "ino": info.st_ino},
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return mountpoint, identity, hashlib.sha256(encoded).hexdigest()


def _children(path, budget):
    if time.monotonic() > budget["deadline"]:
        raise ModelCacheVolumeError("cache metadata audit timed out")
    try:
        rows = []
        with os.scandir(path) as entries:
            for entry in entries:
                if time.monotonic() > budget["deadline"]:
                    raise ModelCacheVolumeError("metadata audit timed out")
                budget["entries"] += 1
                if budget["entries"] > _MAX_ENTRIES:
                    raise ModelCacheVolumeError("cache metadata audit exceeds entry limit")
                rows.append(entry)
        rows.sort(key=lambda entry: entry.name)
    except OSError as exc:
        raise ModelCacheVolumeError("cannot read cache metadata") from exc
    return rows


def _read_metadata(path, limit, *, binary=False):
    """Read a bounded regular file without following a substituted leaf."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ModelCacheVolumeError("metadata is not a bounded regular file")
            raw = stream.read(limit + 1)
            if len(raw) > limit or model_cache_native._binding(before) != model_cache_native._binding(os.fstat(stream.fileno())):
                raise ModelCacheVolumeError("metadata changed during audit")
        return raw if binary else raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelCacheVolumeError("cannot read bounded metadata") from exc


def _overlaps(first, second):
    return os.path.commonpath((str(first), str(second))) in {str(first), str(second)}


def _config_strings(document):
    # The image auditor's walker omits scalar list entries (Compose short mounts).
    pending = [((), document)]
    visited = 0
    while pending:
        field, value = pending.pop()
        visited += 1
        if visited > _MAX_ENTRIES or len(field) > 32:
            raise ModelCacheVolumeError("operator configuration exceeds structure limits")
        if isinstance(value, str):
            yield ".".join(field), value
        elif isinstance(value, dict):
            pending.extend(((*field, str(key)), child) for key, child in value.items())
        elif isinstance(value, list):
            pending.extend(((*field, str(index)), child) for index, child in enumerate(value))


def _agent_registry(raw):
    """Validate the public Hub agent-detection registry, never account settings."""
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ModelCacheVolumeError("invalid public agent-harness registry") from exc
    def env_name(name):
        return isinstance(name, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", name)
    if (not isinstance(value, dict) or set(value) != {"standardEnvVars", "harnesses"}
            or not isinstance(value["standardEnvVars"], list) or len(value["standardEnvVars"]) > 32
            or not all(env_name(name) for name in value["standardEnvVars"])
            or not isinstance(value["harnesses"], dict) or len(value["harnesses"]) > 256):
        raise ModelCacheVolumeError("invalid public agent-harness registry schema")
    for name, fields in value["harnesses"].items():
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) or not isinstance(fields, dict)
                or not set(fields) <= {"prettyLabel", "description", "docsUrl", "repoUrl", "envVars"}):
            raise ModelCacheVolumeError("invalid public agent-harness registry entry")
        for key, item in fields.items():
            if key == "envVars":
                if (not isinstance(item, dict) or len(item) > 32
                        or any(not env_name(k) or not isinstance(v, str)
                               or not re.fullmatch(r"\*|[A-Za-z0-9._/-]{1,128}", v) for k, v in item.items())):
                    raise ModelCacheVolumeError("invalid public agent-harness environment patterns")
            elif not isinstance(item, str) or len(item) > 4096 or any(ord(c) < 32 for c in item):
                raise ModelCacheVolumeError("invalid public agent-harness description")
            elif key in {"docsUrl", "repoUrl"}:
                try:
                    url = urllib.parse.urlsplit(item)
                except ValueError as exc:
                    raise ModelCacheVolumeError("invalid public agent-harness URL") from exc
                if url.scheme != "https" or not url.netloc or url.username or url.password:
                    raise ModelCacheVolumeError("invalid public agent-harness URL")


def _auxiliary_cache(entries, snapshot, links, payloads, revision, budget):
    """Recognize only bounded generated HF metadata observed in dedicated caches."""
    inventory = []
    total = 0
    kernels = {}
    by_path = {path: info for path, info in entries.values()}
    module_prefix = "modules/transformers_modules/_" + revision
    tree_prefix = "hub/" + snapshot.parent.parent.name + "/trees"
    for relative, (path, info) in sorted(entries.items()):
        if time.monotonic() > budget["deadline"]:
            raise ModelCacheVolumeError("auxiliary cache audit timed out")
        directory = stat.S_ISDIR(info.st_mode)
        regular = stat.S_ISREG(info.st_mode)
        kind = None
        raw = None
        limit = 1024 * 1024
        if relative in {".check_for_update_done", ".check_for_skill_update_done"} and regular and info.st_size == 0:
            kind = "hf-update-marker"
        elif relative == "hub/blobs/.huggingface-shared-blobs" and regular:
            raw = _read_metadata(path, 2, binary=True)
            if raw != b"1\n":
                raise ModelCacheVolumeError("unrecognized HF shared blob marker")
            kind = "hf-shared-blob-marker"
        elif relative == "hub/CACHEDIR.TAG" and regular:
            raw = _read_metadata(path, 1024, binary=True)
            expected = (b"Signature: 8a477f597d28d172789f06886806bc55\n"
                        b"# This file is a cache directory tag created by huggingface_hub.\n"
                        b"# For information about cache directory tags, see:\n#\thttps://bford.info/cachedir/\n")
            if raw != expected:
                raise ModelCacheVolumeError("unrecognized HF cache directory tag")
            kind = "hf-cache-tag"
        elif relative == ".agent_harnesses.json" and regular:
            raw = _read_metadata(path, 64 * 1024, binary=True)
            _agent_registry(raw)
            kind = "public-agent-harness-registry"
        elif relative == tree_prefix and directory:
            kind = "selected-tree-directory"
        elif relative == tree_prefix + "/" + revision + ".json" and regular:
            raw = _read_metadata(path, limit, binary=True)
            try:
                tree = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise ModelCacheVolumeError("invalid selected tree metadata") from exc
            expected = {p.relative_to(snapshot).as_posix(): p for p in links}
            if (not isinstance(tree, dict) or set(tree) != {"format_version", "files"}
                    or type(tree["format_version"]) is not int or tree["format_version"] != 1
                    or not isinstance(tree["files"], dict) or set(tree["files"]) != set(expected)):
                raise ModelCacheVolumeError("tree metadata does not match exact selected snapshot")
            for name, fields in tree["files"].items():
                target = links[expected[name]]
                if (not isinstance(fields, dict) or not {"size", "blob_id"} <= set(fields)
                        or not set(fields) <= {"size", "blob_id", "lfs_sha256", "lfs_size", "xet_hash"}
                        or type(fields["size"]) is not int or fields["size"] != payloads[target].st_size
                        or not isinstance(fields["blob_id"], str) or not _REVISION.fullmatch(fields["blob_id"])):
                    raise ModelCacheVolumeError("invalid selected tree file metadata")
                has_lfs = "lfs_sha256" in fields
                if (has_lfs != ("lfs_size" in fields)
                        or (has_lfs and (not isinstance(fields["lfs_sha256"], str)
                            or not re.fullmatch(r"[0-9a-f]{64}", fields["lfs_sha256"])
                            or type(fields["lfs_size"]) is not int or fields["lfs_size"] != fields["size"]))
                        or ("xet_hash" in fields and (not has_lfs or not isinstance(fields["xet_hash"], str)
                            or not re.fullmatch(r"[0-9a-f]{64}", fields["xet_hash"])))):
                    raise ModelCacheVolumeError("invalid selected tree download metadata")
                digest = fields.get("xet_hash", fields.get("lfs_sha256", fields["blob_id"]))
                if target.name != digest:
                    raise ModelCacheVolumeError("selected tree hash differs from snapshot target")
            kind = "selected-snapshot-tree-metadata"
        elif relative.startswith("modules"):
            if relative in {"modules", "modules/transformers_modules", module_prefix} and directory:
                kind = "selected-module-directory"
            elif relative in {"modules/__init__.py", "modules/transformers_modules/__init__.py", module_prefix + "/__init__.py"} and regular and info.st_size == 0:
                kind = "empty-module-initializer"
            elif relative.startswith(module_prefix + "/"):
                tail = relative[len(module_prefix) + 1:].split("/")
                if re.fullmatch(r"[0-9a-f]{16}", tail[0]):
                    if directory and (len(tail) == 1 or (len(tail) == 2 and tail[1] == "__pycache__")):
                        kind = "selected-module-directory"
                    elif regular and len(tail) == 2 and tail[1] == "__init__.py" and info.st_size == 0:
                        kind = "empty-module-initializer"
                    elif regular and len(tail) == 2 and re.fullmatch(r"[A-Za-z_]\w*\.py", tail[1]):
                        source = snapshot / tail[1]
                        if source not in links:
                            raise ModelCacheVolumeError("generated module is absent from selected snapshot")
                        raw = _read_metadata(path, limit, binary=True)
                        if raw != _read_metadata(links[source], limit, binary=True):
                            raise ModelCacheVolumeError("generated module differs from selected snapshot")
                        kind = "selected-snapshot-module-copy"
                    elif regular and len(tail) == 3 and tail[1] == "__pycache__":
                        match = re.fullmatch(r"([A-Za-z_]\w*)\.cpython-312\.pyc", tail[2])
                        if match:
                            source = path.parent.parent / (match.group(1) + ".py")
                            if source not in by_path or not stat.S_ISREG(by_path[source].st_mode):
                                raise ModelCacheVolumeError("bytecode lacks its selected module source")
                            raw = _read_metadata(path, limit, binary=True)
                            source_info = by_path[source]
                            if (len(raw) < 16 or raw[:4] != b"\xcb\r\r\n" or raw[4:8] != bytes(4)
                                    or int.from_bytes(raw[8:12], "little") != (int(source_info.st_mtime) & 0xffffffff)
                                    or int.from_bytes(raw[12:16], "little") != source_info.st_size):
                                raise ModelCacheVolumeError("bytecode header does not match selected module source")
                            kind = "selected-module-bytecode"
        elif relative.startswith("hub/kernels--"):
            parts = relative.split("/")
            encoded = parts[1].split("--")
            if len(encoded) == 3 and _REPO.fullmatch("/".join(encoded[1:])):
                repo = "/".join(encoded[1:])
                item = kernels.setdefault(repo, {"repo_id": repo, "ref_revision": None, "miss_revisions": set()})
                if directory and (len(parts) == 2 or (len(parts) == 3 and parts[2] in {"refs", ".no_exist"})):
                    kind = "kernel-miss-directory"
                elif regular and parts[2:] == ["refs", "main"]:
                    ref = _read_metadata(path, 128).strip()
                    if not _REVISION.fullmatch(ref):
                        raise ModelCacheVolumeError("invalid kernel miss ref")
                    item["ref_revision"] = ref
                    kind = "kernel-miss-ref"
                elif len(parts) >= 4 and parts[2] == ".no_exist" and _REVISION.fullmatch(parts[3]):
                    if directory and len(parts) == 4:
                        marker = entries.get(relative + "/kernel-status.toml")
                        if marker is None or not stat.S_ISREG(marker[1].st_mode) or marker[1].st_size:
                            raise ModelCacheVolumeError("kernel miss directory lacks its empty marker")
                        kind = "kernel-miss-directory"
                    elif regular and len(parts) == 5 and parts[4] == "kernel-status.toml" and info.st_size == 0:
                        item["miss_revisions"].add(parts[3])
                        kind = "empty-kernel-miss-marker"
        elif relative.startswith("xet"):
            parts = relative.split("/")
            if directory and (parts == ["xet"] or parts == ["xet", "logs"]
                              or (len(parts) in {2, 3} and re.fullmatch(r"https___[A-Za-z0-9_-]{1,96}", parts[1])
                                  and (len(parts) == 2 or parts[2] == "staging"))):
                kind = "xet-download-directory"
            elif regular and len(parts) == 3 and parts[:2] == ["xet", "logs"] and re.fullmatch(r"xet_[0-9]{8}T[0-9]{9}\+[0-9]{4}_[0-9]+\.log", parts[2]):
                kind = "xet-download-log"
                limit = 8 * 1024 * 1024
        if kind is None or not (directory or regular):
            raise ModelCacheVolumeError("dedicated cache contains another repository, revision, or unknown data at: %s" % relative)
        record = {"path": relative, "kind": kind, "bytes": info.st_size if regular else 0}
        if regular:
            total += info.st_size
            if total > 32 * 1024 * 1024:
                raise ModelCacheVolumeError("auxiliary cache exceeds total byte limit")
            if raw is None:
                raw = _read_metadata(path, limit, binary=True)
            record["content_sha256"] = hashlib.sha256(raw).hexdigest()
        inventory.append(record)
    for item in kernels.values():
        if item["ref_revision"] not in item["miss_revisions"]:
            raise ModelCacheVolumeError("kernel miss ref lacks its exact empty marker")
        item["miss_revisions"] = sorted(item["miss_revisions"])
    return {"entries": inventory, "file_count": sum("content_sha256" in row for row in inventory),
            "logical_bytes": total, "kernel_miss_caches": list(kernels.values()),
            "module_revision": revision if any(row["kind"].startswith("selected-module") for row in inventory) else None}


def _cache_identity(mountpoint, repo_id, revision):
    """Prove every payload belongs to the one revision, without reading weights."""
    root = Path(mountpoint)
    hub = root / "hub"
    selected = "models--" + repo_id.replace("/", "--")
    model = hub / selected
    budget = {"entries": 0, "deadline": time.monotonic() + _MAX_SECONDS}
    root_info = root.lstat()
    entries = {}
    pending = [root]
    while pending:
        current = pending.pop()
        for row in _children(current, budget):
            path = Path(row.path)
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if info.st_dev != root_info.st_dev or os.path.ismount(path):
                raise ModelCacheVolumeError("cache crosses a filesystem boundary")
            if len(path.relative_to(root).parts) > 32:
                raise ModelCacheVolumeError("cache exceeds directory depth limit")
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)) or info.st_nlink != 1:
                raise ModelCacheVolumeError("cache contains an unsafe file")
            entries[relative] = (path, info)

    required = {"hub", "hub/" + selected, "hub/" + selected + "/snapshots",
                "hub/" + selected + "/snapshots/" + revision, "hub/blobs"}
    if any(name not in entries or not stat.S_ISDIR(entries[name][1].st_mode) for name in required):
        raise ModelCacheVolumeError("dedicated cache lacks the exact selected revision")
    global_blobs = {}
    local_blobs = {}
    aliases = {}
    links = {}
    companions = {}
    bindings = []
    auxiliary_entries = {}
    digest = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
    for relative, (path, info) in sorted(entries.items()):
        parts = relative.split("/")
        directory = stat.S_ISDIR(info.st_mode)
        link = stat.S_ISLNK(info.st_mode)
        regular = stat.S_ISREG(info.st_mode)
        accepted = relative in required and directory
        if relative == "hub":
            # Managed apply may create .locks; the hub inode still must match.
            binding = [info.st_dev, info.st_ino, info.st_mode]
        else:
            binding = model_cache_native._binding(info)
        if parts[:2] == ["hub", "blobs"]:
            if len(parts) == 3 and directory and re.fullmatch(r"[0-9a-f]{2}", parts[2]):
                accepted = True
            elif len(parts) == 4 and regular:
                match = re.fullmatch("(" + digest + r")(?:(\.lock|\.refs))?", parts[3])
                if match and parts[2] == match.group(1)[:2]:
                    accepted = True
                    if match.group(2):
                        if info.st_size > 16 * 1024:
                            raise ModelCacheVolumeError("global blob companion exceeds size limit")
                        companions.setdefault(path.with_suffix(""), {})[path.suffix] = path
                    else:
                        global_blobs[path] = info
        elif parts[:2] == ["hub", selected]:
            if relative == "hub/" + selected + "/blobs" and directory:
                accepted = True
            elif len(parts) == 4 and parts[2] == "blobs" and link and re.fullmatch(digest, parts[3]):
                aliases[path] = Path(os.path.realpath(path))
                accepted = True
            elif len(parts) == 4 and parts[2] == "blobs" and regular and re.fullmatch(digest, parts[3]):
                local_blobs[path] = info
                accepted = True
            elif parts[2:4] == ["snapshots", revision] and len(parts) > 4:
                accepted = directory or link
                if link:
                    if os.path.isabs(os.readlink(path)):
                        raise ModelCacheVolumeError("snapshot contains an absolute symlink")
                    links[path] = Path(os.path.realpath(path))
            elif len(parts) == 3 and parts[2] == "refs" and directory:
                accepted = True
            elif len(parts) == 4 and parts[2] == "refs" and regular:
                accepted = _read_metadata(path, 1024).strip() == revision
        elif parts[:2] == ["hub", ".locks"]:
            accepted = directory and (len(parts) == 2 or (len(parts) == 3 and parts[2] in {selected, "anvil-serving"}))
            if len(parts) == 4 and regular and info.st_size <= 1:
                accepted = ((parts[2] == selected and re.fullmatch(digest + r"\.lock", parts[3]))
                            or (parts[2:] == ["anvil-serving", "native-pull.lock"]))
            if accepted:
                # The descriptor fence separately binds all legacy lock metadata.
                continue
        if not accepted:
            auxiliary_entries[relative] = (path, info)
        bindings.append([relative, binding, os.readlink(path) if link else None])
    if not global_blobs or set(companions) != set(global_blobs):
        raise ModelCacheVolumeError("global blobs lack exact lock/reference companions")
    # HF can keep small files in the repository while sharing large blobs.
    # Both stores must be consumed completely by this exact snapshot.
    payloads = global_blobs | local_blobs
    if not links or set(links.values()) != set(payloads):
        raise ModelCacheVolumeError("blob stores contain data not selected by the exact snapshot or missing targets")
    if any(target not in global_blobs for target in aliases.values()):
        raise ModelCacheVolumeError("repository blob alias escapes global blob store")
    for path in aliases:
        target = os.readlink(path)
        if os.path.isabs(target) or Path(os.path.abspath(path.parent / target)) not in global_blobs:
            raise ModelCacheVolumeError("repository blob alias escapes the declared topology")
    for path in links:
        target = Path(os.path.abspath(path.parent / os.readlink(path)))
        if target not in aliases and target not in payloads:
            raise ModelCacheVolumeError("snapshot link escapes the declared topology")
    for blob, metadata in companions.items():
        if set(metadata) != {".lock", ".refs"}:
            raise ModelCacheVolumeError("global blobs lack exact lock/reference companions")
        references = _read_metadata(metadata[".refs"], 16 * 1024).splitlines()
        expected = {path.relative_to(hub).as_posix() for path, target in aliases.items() if target == blob}
        if not references or len(references) != len(set(references)) or set(references) != expected:
            raise ModelCacheVolumeError("global blob references another repository or unknown alias")
    auxiliary = _auxiliary_cache(auxiliary_entries, model / "snapshots" / revision, links, payloads, revision, budget)
    fingerprint = hashlib.sha256(json.dumps([bindings, auxiliary], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"repo_id": repo_id, "revision": revision, "snapshot_links": len(links),
            "layout": "global-hub-blobs", "logical_bytes": sum(info.st_size for info in payloads.values()),
            "global_blob_count": len(global_blobs), "repository_blob_count": len(local_blobs),
            "global_blob_bytes": sum(info.st_size for info in global_blobs.values()),
            "repository_blob_bytes": sum(info.st_size for info in local_blobs.values()),
            "fingerprint": fingerprint, "auxiliary_cache": auxiliary}


def _container_refs(volume, mountpoint, *, non_model_root_views=(), runner):
    raw = _run(["container", "ls", "--all", "--quiet", "--no-trunc"], runner=runner).stdout
    ids = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(ids) > _MAX_ENTRIES or len(ids) != len(set(ids)) or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise ModelCacheVolumeError("Docker container audit returned incomplete inventory")
    declared = set(non_model_root_views)
    if len(declared) != len(tuple(non_model_root_views)) or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in declared):
        raise ModelCacheVolumeError("declared non-model root-view IDs must be unique full Docker IDs")
    if not declared.issubset(ids):
        raise ModelCacheVolumeError("declared non-model root-view container is absent from Docker inventory")
    if not ids:
        return []
    result = _run(["container", "inspect", "--format", "{{.Id}}\t{{json .Mounts}}\t{{json .HostConfig.DeviceRequests}}", *ids], runner=runner)
    refs = []
    seen = set()
    validated_observers = set()
    for line in result.stdout.splitlines():
        try:
            container_id, mounts, devices = line.split("\t", 2)
            mounts = json.loads(mounts)
            devices = json.loads(devices)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ModelCacheVolumeError("Docker container mount audit returned invalid data") from exc
        if container_id not in ids or container_id in seen or not isinstance(mounts, list):
            raise ModelCacheVolumeError("Docker container mount audit returned incomplete inventory")
        seen.add(container_id)
        for mount in mounts:
            if not isinstance(mount, dict) or not isinstance(mount.get("Type"), str):
                raise ModelCacheVolumeError("Docker container mount audit returned invalid mount")
            source = mount.get("Source")
            if mount["Type"] not in {"volume", "bind", "tmpfs", "npipe", "cluster"}:
                raise ModelCacheVolumeError("Docker container mount audit returned unknown mount type")
            if mount["Type"] == "volume":
                if not isinstance(mount.get("Name"), str) or not mount["Name"] or not isinstance(source, str) or not os.path.isabs(source):
                    raise ModelCacheVolumeError("Docker container mount audit returned invalid volume mount")
            if mount["Type"] == "bind" and not isinstance(source, str):
                raise ModelCacheVolumeError("Docker container mount audit returned invalid bind mount")
            if mount["Type"] == "volume" and mount.get("Name") == volume:
                refs.append({"container_id": container_id, "kind": "volume"})
            elif mount["Type"] in {"bind", "volume"}:
                if not os.path.isabs(source):
                    raise ModelCacheVolumeError("Docker bind mount audit returned a non-absolute source")
                lexical = os.path.abspath(source)
                overlap = _overlaps(lexical, mountpoint) or _overlaps(os.path.realpath(lexical), mountpoint)
                observer = (mount["Type"] == "bind" and container_id in declared and source == os.path.sep
                            and mount.get("RW") is False and devices in (None, [])
                            and isinstance(mount.get("Destination"), str) and os.path.isabs(mount["Destination"]))
                if observer:
                    validated_observers.add(container_id)
                if overlap and not observer:
                    refs.append({"container_id": container_id, "kind": "bind"})
    if seen != set(ids) or validated_observers != declared:
        raise ModelCacheVolumeError("Docker container mount audit returned incomplete inventory")
    return refs


def _config_refs(home, *, volume, repo_id, revision, mountpoint, runner, model_identities=(), bindings=None):
    refs = []
    budget = {"entries": 0, "deadline": time.monotonic() + _MAX_SECONDS}
    pending = [home]
    documents = []
    dependencies = []
    audited = set()
    identities = {value for value in (volume, repo_id, revision, str(mountpoint), *model_identities) if value}
    identity_pattern = re.compile(r'(?<![A-Za-z0-9_.-])(?:' + '|'.join(
        re.escape(value) for value in sorted(identities, key=len, reverse=True)
    ) + r')(?![A-Za-z0-9_.-])')
    while pending:
        current = pending.pop()
        for row in _children(current, budget):
            path = Path(row.path)
            info = path.lstat()
            # The operator's conventional secret file is opaque. It cannot be
            # a delegated manifest (unsupported suffix), and unresolved cache
            # variables in actual manifests still block the audit below.
            if current == home and row.name == ".env" and (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                continue
            if stat.S_ISDIR(info.st_mode):
                if row.name in docker_images._NON_OPERATIONAL_CONFIG_DIRS or (row.name == "recovered-configs" and current == home):
                    continue
                pending.append(path)
            elif stat.S_ISLNK(info.st_mode):
                raise ModelCacheVolumeError("operator configuration audit contains a symlink")
            elif path.suffix.lower() in _CONFIG_SUFFIXES:
                documents.append(path)
    for path in sorted(documents):
        if time.monotonic() > budget["deadline"]:
            raise ModelCacheVolumeError("operator configuration audit timed out")
        raw = _read_metadata(path, docker_images.MAX_CONFIG_BYTES)
        if bindings is not None:
            bindings.append([path.relative_to(home).as_posix(), model_cache_native._binding(path.lstat()),
                             hashlib.sha256(raw.encode()).hexdigest()])
        try:
            if path.suffix.lower() == ".toml":
                document = tomllib.loads(raw)
            elif path.suffix.lower() == ".json":
                document = json.loads(raw)
            else:
                timeout = max(0.1, min(30, budget["deadline"] - time.monotonic()))
                if docker_images._yaml_requires_compose_validation(path, raw):
                    document = docker_images._compose_config_payload(path, runner=runner, timeout=timeout)
                else:
                    document = docker_images._standalone_yaml_config_payload(path, raw, runner=runner, timeout=timeout)
        except (OSError, ValueError, RecursionError) as exc:
            raise ModelCacheVolumeError("operator configuration audit is incomplete") from exc
        if not isinstance(document, (dict, list)):
            raise ModelCacheVolumeError("operator configuration has an invalid root")
        audited.add(path)
        for field, value in _config_strings(document):
            if identity_pattern.search(value):
                refs.append({"path": path.relative_to(home).as_posix(), "field": field})
            if _PATH_FIELD.search(field.lower()):
                if "$" in value:
                    raise ModelCacheVolumeError("operator configuration contains an unresolved cache reference")
                # A Compose short mount's first component is its source. Named
                # sources were handled above; ordinary relative paths use the file.
                source = value.split(":", 1)[0] if value.startswith(("/", ".", "~")) else value
                candidate = Path(os.path.expanduser(source))
                if not candidate.is_absolute():
                    candidate = path.parent / candidate
                lexical = Path(os.path.abspath(candidate))
                resolved = Path(os.path.realpath(lexical))
                if _overlaps(lexical, mountpoint) or _overlaps(resolved, mountpoint):
                    refs.append({"path": path.relative_to(home).as_posix(), "field": field})
            if docker_images._is_manifest_path(field, document=document):
                if "$" in value or not value:
                    raise ModelCacheVolumeError("operator configuration dependency is unresolved")
                if re.fullmatch(r"(?:serve|promotion)\.[0-9]+\.(?:rollback_)?router_config", field):
                    value = value.replace("{dir}", str(path.parent))
                target = Path(os.path.expanduser(value))
                if not target.is_absolute():
                    target = path.parent / target
                target = Path(os.path.abspath(target))
                if target.suffix.lower() not in _CONFIG_SUFFIXES:
                    raise ModelCacheVolumeError("operator configuration dependency has unsupported format at %s:%s" %
                                                (path.relative_to(home).as_posix(), field))
                # An external spelling may be an operator-home compatibility
                # alias. Only its canonical, already-audited file is accepted.
                if target.is_relative_to(home):
                    lexical_parts = target.relative_to(home).parts
                    if (any(part in docker_images._NON_OPERATIONAL_CONFIG_DIRS for part in lexical_parts)
                            or (lexical_parts and lexical_parts[0] == "recovered-configs")):
                        raise ModelCacheVolumeError("operator configuration dependency references an excluded path")
                target = Path(os.path.realpath(target))
                try:
                    relative = target.relative_to(home)
                except ValueError as exc:
                    raise ModelCacheVolumeError("operator configuration dependency escapes config home") from exc
                if (any(part in docker_images._NON_OPERATIONAL_CONFIG_DIRS for part in relative.parts)
                        or (relative.parts and relative.parts[0] == "recovered-configs")):
                    raise ModelCacheVolumeError("operator configuration dependency references an excluded path")
                dependencies.append(target)
    missing = sorted({str(path.relative_to(home)) for path in dependencies if path not in audited})
    if missing:
        raise ModelCacheVolumeError("operator configuration dependency is missing or unaudited: " + ", ".join(missing))
    return refs


def _native_fd_refs(mountpoint):
    if os.name != "posix" or not Path("/proc").is_dir() or os.geteuid() != 0:
        raise ModelCacheVolumeError("native writer audit requires root access to POSIX /proc")
    refs = []
    budget = {"entries": 0, "deadline": time.monotonic() + _MAX_SECONDS}
    descriptor_entries = 0
    mapping_entries = 0
    for process in _children(Path("/proc"), budget):
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            # A process may exit between enumeration and opening its fd directory.
            with os.scandir(Path(process.path) / "fd") as descriptors:
                for descriptor in descriptors:
                    # Descriptor counts are independent of process/tree counts.
                    descriptor_entries += 1
                    if descriptor_entries > _MAX_NATIVE_DESCRIPTORS or time.monotonic() > budget["deadline"]:
                        raise ModelCacheVolumeError("native writer audit exceeds its bound")
                    try:
                        target = os.readlink(descriptor.path)
                    except FileNotFoundError:
                        continue
                    if target == str(mountpoint) or target.startswith(str(mountpoint) + os.sep):
                        refs.append({"pid": process.name})
            # An mmap can outlive its file descriptor. Open-FD checks alone
            # would wrongly classify a still-mapped model as unused.
            mappings = _read_metadata(Path(process.path) / "maps", 4 * 1024 * 1024)
            for mapping in mappings.splitlines():
                # CUDA and browser processes routinely have tens of thousands
                # of mappings. Keep their separate bounded budget rather than
                # exhausting the process/descriptor budget on the first owner.
                mapping_entries += 1
                if mapping_entries > _MAX_NATIVE_MAPPINGS or time.monotonic() > budget["deadline"]:
                    raise ModelCacheVolumeError("native writer audit exceeds its bound")
                fields = mapping.split(None, 5)
                if len(fields) == 6:
                    target = fields[5].removesuffix(" (deleted)")
                    if target == str(mountpoint) or target.startswith(str(mountpoint) + os.sep):
                        refs.append({"pid": process.name, "kind": "mapping"})
            for name in ("cwd", "exe"):
                try:
                    target = os.readlink(Path(process.path) / name)
                except FileNotFoundError:
                    continue
                if target == str(mountpoint) or target.startswith(str(mountpoint) + os.sep):
                    refs.append({"pid": process.name, "kind": name})
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ModelCacheVolumeError("native writer audit is incomplete") from exc
    return refs


def _check_hf_locks(mountpoint):
    """Reject currently-held HF locks without creating any lock path in preview."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise ModelCacheVolumeError("dedicated volume deletion requires POSIX no-follow descriptors")
    root_fd = None
    descriptors = []
    try:
        root_fd = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        hub_fd = model_cache_native._open_directory("hub", root_fd)
        descriptors.append(hub_fd)
        lock_fds, _root, _dirs, _files = model_cache_native._acquire_hf_lock_fence(hub_fd)
        descriptors.extend(lock_fds)
        blob_locks = _blob_lock_fence(Path(mountpoint) / "hub" / "blobs")
        descriptors.extend(item[0] for item in blob_locks)
    except (OSError, model_cache_native.NativeCacheError) as exc:
        raise ModelCacheVolumeError("native cache lock audit failed: %s" % exc) from exc
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)
        if root_fd is not None:
            os.close(root_fd)


def _blob_lock_fence(blobs):
    """Keep every global blob lock descriptor until mutation or failure cleanup."""
    budget = {"entries": 0, "deadline": time.monotonic() + _MAX_SECONDS}
    pending = [Path(blobs)]
    records = []
    try:
        while pending:
            for row in _children(pending.pop(), budget):
                path = Path(row.path)
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    continue
                if not row.name.endswith(".lock"):
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ModelCacheVolumeError("global blob lock is unsafe")
                descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    binding = model_cache_native._binding(info)
                    if model_cache_native._binding(os.fstat(descriptor)) != binding:
                        raise ModelCacheVolumeError("global blob lock changed during fencing")
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BaseException:
                    os.close(descriptor)
                    raise
                records.append((descriptor, path, binding))
        return records
    except BaseException as exc:
        for descriptor, _path, _binding in reversed(records):
            os.close(descriptor)
        if isinstance(exc, OSError):
            raise ModelCacheVolumeError("global blob lock is active or unreadable") from exc
        raise


def _verify_blob_lock_fence(records):
    for descriptor, path, expected in records:
        if (model_cache_native._binding(path.lstat()) != expected
                or model_cache_native._binding(os.fstat(descriptor)) != expected):
            raise ModelCacheVolumeError("global blob lock tree changed before mutation")


@contextmanager
def _native_fences(mountpoint, repo_id, expected_root):
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise ModelCacheVolumeError("dedicated volume deletion requires POSIX no-follow descriptors")
    root_fd = None
    locks = []
    try:
        root_fd = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        current = os.fstat(root_fd)
        if (current.st_dev, current.st_ino) != (expected_root["filesystem"]["dev"], expected_root["filesystem"]["ino"]):
            raise ModelCacheVolumeError("Docker volume mountpoint changed before native fencing")
        if fcntl is None:
            raise ModelCacheVolumeError("dedicated volume deletion requires POSIX root fencing")
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ModelCacheVolumeError("Docker volume root is actively fenced") from exc
        hub_fd = model_cache_native._open_directory("hub", root_fd)
        locks.append(hub_fd)
        # Ensure the product writer lock exists, then release this descriptor:
        # the HF fence below reacquires and retains native-pull.lock alongside
        # every other legacy lock through yield/removal. A writer entering the
        # handoff gap blocks that fence; finished changes face the second audit.
        with model_pull_native._writer(Path(mountpoint) / "hub", repo_id.replace("/", "--")):
            pass
        lock_fds, lock_root, lock_dirs, lock_files = model_cache_native._acquire_hf_lock_fence(hub_fd)
        locks.extend(lock_fds)
        blob_locks = _blob_lock_fence(Path(mountpoint) / "hub" / "blobs")
        locks.extend(item[0] for item in blob_locks)
        yield hub_fd, lock_root, lock_dirs, lock_files, blob_locks
    except (OSError, model_cache_native.NativeCacheError, model_pull_native.NativePullError) as exc:
        raise ModelCacheVolumeError("native cache fence failed: %s" % exc) from exc
    finally:
        for descriptor in reversed(locks): os.close(descriptor)
        if root_fd is not None:
            os.close(root_fd)


def inspect(repo_id, revision, volume, config_home, *, non_model_root_views=(), runner=subprocess.run, _check_locks=True):
    """Return a bounded, non-mutating dedicated-volume deletion audit."""
    if os.name != "posix" or fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise ModelCacheVolumeError("dedicated volume deletion requires POSIX no-follow descriptors")
    try:
        _valid(repo_id, revision, volume); home = _config_home(config_home)
        mountpoint, identity, identity_fingerprint = _volume(volume, runner=runner)
        cache = _cache_identity(mountpoint, repo_id, revision)
        fingerprint = hashlib.sha256(json.dumps({"volume": identity_fingerprint, "cache": cache["fingerprint"]},
                                                 sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if _check_locks:
            _check_hf_locks(mountpoint)
        containers = _container_refs(volume, mountpoint, non_model_root_views=non_model_root_views, runner=runner)
        configured = _config_refs(home, volume=volume, repo_id=repo_id, revision=revision, mountpoint=mountpoint, runner=runner)
        native_fds = _native_fd_refs(mountpoint)
        blocked = []
        if containers: blocked.append("referenced by a running or stopped Docker container")
        if configured: blocked.append("referenced by current operator configuration")
        if native_fds: blocked.append("open by a native host process")
        return {"schema": "dedicated-model-cache-volume-removal/v1", "repo_id": repo_id,
                "revision": revision, "volume": identity, "volume_identity_fingerprint": identity_fingerprint,
                "volume_fingerprint": fingerprint,
                "cache": cache, "references": {"containers": containers, "configured": configured,
                "native_fd_processes": native_fds}, "blocked_reasons": blocked,
                "removable": not blocked, "recovery": "re-download the exact revision into a new dedicated cache volume",
                "logical_reclaimable_bytes": cache["logical_bytes"],
                "auxiliary_cache": cache["auxiliary_cache"],
                "actual_free_space_delta": "measured separately after volume removal"}
    except OSError as exc:
        raise ModelCacheVolumeError("cache audit could not be completed") from exc



def remove(repo_id, revision, volume, config_home, *, expected_fingerprint, writers_quiesced=False,
           auxiliary_caches_reviewed=False,
           non_model_root_views=(), confirm=False, dry_run=False, runner=subprocess.run):
    """Preview or delete one twice-audited dedicated cache volume, without force."""
    first = inspect(repo_id, revision, volume, config_home, non_model_root_views=non_model_root_views, runner=runner)
    result = {"schema": first["schema"], "applied": False, "dry_run": bool(dry_run),
              "outcome": "blocked" if first["blocked_reasons"] else "preview", "inspection": first,
              "native_writer_quiescence": "process-FD snapshot and managed/HF lock fences; uncooperative host writers must remain quiescent"}
    if dry_run or first["blocked_reasons"] or not confirm:
        return result
    if not writers_quiesced:
        raise ModelCacheVolumeError("apply requires --writers-quiesced after the resource owner stops native writers")
    if not isinstance(expected_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
        raise ModelCacheVolumeError("apply requires the preview's --expected-volume-fingerprint")
    if expected_fingerprint != first["volume_fingerprint"]:
        result["outcome"] = "identity-drift"; return result
    if first["auxiliary_cache"]["entries"] and not auxiliary_caches_reviewed:
        raise ModelCacheVolumeError("apply requires --auxiliary-caches-reviewed for the fingerprinted auxiliary inventory")
    mountpoint = Path(first["volume"]["mountpoint"])
    space_anchor = mountpoint.parent.parent
    if space_anchor.stat().st_dev != mountpoint.stat().st_dev:
        raise ModelCacheVolumeError("cannot establish a surviving free-space measurement path")
    usage = os.statvfs(space_anchor)
    before_free = usage.f_bavail * usage.f_frsize
    with _native_fences(mountpoint, repo_id, first["volume"]) as (hub_fd, lock_root, lock_dirs, lock_files, blob_locks):
        second = inspect(repo_id, revision, volume, config_home, non_model_root_views=non_model_root_views,
                         runner=runner, _check_locks=False)
        result["verification"] = second
        if second["blocked_reasons"]:
            result["outcome"] = "blocked"; return result
        if second["volume_fingerprint"] != first["volume_fingerprint"]:
            result["outcome"] = "identity-drift"; return result
        if second["cache"]["fingerprint"] != first["cache"]["fingerprint"]:
            result["outcome"] = "identity-drift"; return result
        try:
            model_cache_native._verify_hf_lock_fence(hub_fd, lock_root, lock_dirs, lock_files)
            _verify_blob_lock_fence(blob_locks)
        except (model_cache_native.NativeCacheError, ModelCacheVolumeError, OSError) as exc:
            result.update({"outcome": "identity-drift", "error": str(exc)}); return result
        try:
            _run(["volume", "rm", volume], runner=runner)
        except _DockerTimeout as exc:
            result.update({"outcome": "unknown", "removal_attempted": True, "error": str(exc)}); return result
        except ModelCacheVolumeError as exc:
            result.update({"outcome": "failed", "removal_attempted": True, "error": str(exc)}); return result
    try:
        _volume(volume, runner=runner)
    except ModelCacheVolumeError as exc:
        if "no such volume" not in str(exc).lower():
            result.update({"outcome": "failed", "removal_attempted": True, "error": "post-removal verification failed: %s" % exc}); return result
    else:
        result.update({"outcome": "failed", "removal_attempted": True, "error": "Docker still resolves the removed volume"}); return result
    if os.path.lexists(mountpoint):
        result.update({"outcome": "failed", "removal_attempted": True, "error": "volume mountpoint still exists after removal"}); return result
    usage = os.statvfs(space_anchor)
    after_free = usage.f_bavail * usage.f_frsize
    result.update({"applied": True, "outcome": "removed", "removed_volume": volume,
                   "actual_free_space_delta_bytes": after_free - before_free,
                   "recovery": first["recovery"]})
    return result
