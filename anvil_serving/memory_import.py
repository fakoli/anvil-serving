"""Explicit, append-free import of curated Markdown into Hindsight."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from .control_plane.mcp.auth_file import AuthFileError, read_private_auth_file
from .control_plane import bootstrap_shim
from .operator_output import CommandResult, SafetyError
from .transports import _NoRedirectHandler


IMPORTER_VERSION = "1"
MAX_SOURCES = 32
MAX_FILES = 128
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_CHUNK_CHARS = 12_000
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_HARNESS = frozenset({"codex", "pi", "hermes", "openclaw", "claude"})
_DEFENSE = {"enabled": True, "rules": [{"on": "sensitive_data", "action": "block"}]}


class MemoryImportError(ValueError):
    """A safe import failure that never carries source content or credentials."""


@dataclass(frozen=True)
class Source:
    id: str
    harness: str
    root: Path
    files: tuple[str, ...]


@dataclass(frozen=True)
class ImportConfig:
    base_url: str
    bank: str
    auth_file: str
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class Chunk:
    source_id: str
    harness: str
    source_file: str
    source_sha256: str
    index: int
    content: str

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def document_id(self) -> str:
        return "memory-import:%s:%s:%s:%d" % (
            self.source_id, self.source_file, self.source_sha256, self.index,
        )


def _safe_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise MemoryImportError("base_url must be a URL")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or not parsed.hostname):
        raise MemoryImportError("base_url must be a credential-free origin without a path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise MemoryImportError("base_url has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise MemoryImportError("base_url port must be in range")
    host = parsed.hostname.lower()
    if parsed.scheme == "http" and host == "127.0.0.1":
        return value.rstrip("/")
    if parsed.scheme != "https" or host == "localhost":
        raise MemoryImportError("base_url must be loopback HTTP or private HTTPS")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not host.endswith(".ts.net"):
            raise MemoryImportError("HTTPS base_url host must be private or tailnet")
    else:
        tailnet = address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10")
        if not (address.is_private or address.is_loopback or tailnet) or address.is_unspecified or address.is_multicast:
            raise MemoryImportError("HTTPS base_url host must be private or tailnet")
    return value.rstrip("/")


def _relative_markdown(value: object) -> str:
    if not isinstance(value, str) or not value.endswith(".md"):
        raise MemoryImportError("source files must be explicit relative .md paths")
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise MemoryImportError("source file path is unsafe")
    return path.as_posix()


def load_config(path: str | Path) -> ImportConfig:
    """Load the exact, public-safe importer schema without reading credentials."""
    try:
        with Path(path).open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MemoryImportError("memory import config is unreadable") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "base_url", "bank", "auth_file", "sources"}:
        raise MemoryImportError("memory import config has an invalid schema")
    if (type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or not isinstance(raw["bank"], str) or _IDENTIFIER.fullmatch(raw["bank"]) is None):
        raise MemoryImportError("memory import config has an invalid bank or schema version")
    auth_file = raw["auth_file"]
    if not isinstance(auth_file, str) or not Path(auth_file).is_absolute():
        raise MemoryImportError("auth_file must be an absolute protected path")
    source_rows = raw["sources"]
    if not isinstance(source_rows, list) or not 1 <= len(source_rows) <= MAX_SOURCES:
        raise MemoryImportError("sources must be a bounded non-empty list")
    sources: list[Source] = []
    seen_ids: set[str] = set()
    total_files = 0
    for row in source_rows:
        if not isinstance(row, dict) or set(row) != {"id", "harness", "root", "files"}:
            raise MemoryImportError("source has an invalid schema")
        source_id, harness, root, files = row["id"], row["harness"], row["root"], row["files"]
        if (not isinstance(source_id, str) or _IDENTIFIER.fullmatch(source_id) is None
                or source_id in seen_ids or not isinstance(harness, str) or harness not in _HARNESS
                or not isinstance(root, str) or not Path(root).is_absolute()
                or ".." in Path(root).parts
                or not isinstance(files, list) or not files):
            raise MemoryImportError("source has invalid identity or files")
        normalized_files = tuple(_relative_markdown(item) for item in files)
        if len(set(normalized_files)) != len(normalized_files):
            raise MemoryImportError("source has duplicate file paths")
        total_files += len(normalized_files)
        if total_files > MAX_FILES:
            raise MemoryImportError("memory import exceeds the file limit")
        seen_ids.add(source_id)
        sources.append(Source(source_id, harness, Path(root), normalized_files))
    return ImportConfig(_safe_base_url(raw["base_url"]), raw["bank"], auth_file, tuple(sources))


def _read_source(root: Path, relative: str) -> bytes:
    """Read one bounded source through held no-follow directory descriptors."""
    path = root / relative
    if ".." in root.parts:
        raise MemoryImportError("source root is unsafe")
    try:
        path.relative_to(root)
    except ValueError as exc:  # defensive against platform-specific paths
        raise MemoryImportError("source file path escapes its root") from exc
    if os.name == "nt":
        try:
            with bootstrap_shim.open_trusted_file(str(path), max_bytes=MAX_FILE_BYTES,
                                                  require_readonly=False) as opened:
                return opened.read_verified()
        except Exception:
            raise MemoryImportError("source file is unavailable or unsafe") from None
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if os.name != "posix" or any(not hasattr(os, name) for name in required):
        raise MemoryImportError("source file safety checks are unavailable")
    descriptors: list[int] = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open("/", directory_flags)
        descriptors.append(descriptor)
        for component in (*root.parts[1:], *Path(relative).parts[:-1]):
            descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            descriptors.append(descriptor)
        file_descriptor = os.open(Path(relative).parts[-1], file_flags, dir_fd=descriptor)
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
            raise MemoryImportError("source file is unavailable or unsafe")
        chunks: list[bytes] = []
        total = 0
        while total <= before.st_size:
            chunk = os.read(file_descriptor, min(64 * 1024, before.st_size + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(file_descriptor)
        if total != before.st_size or (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise MemoryImportError("source file changed while reading")
        return b"".join(chunks)
    except MemoryImportError:
        raise
    except (OSError, OverflowError, ValueError):
        raise MemoryImportError("source file is unavailable or unsafe") from None
    finally:
        while descriptors:
            try:
                os.close(descriptors.pop())
            except OSError:
                pass


def split_markdown(text: str) -> tuple[str, ...]:
    """Split deterministic UTF-8 text at paragraphs where that fits the cap."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHUNK_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind("\n\n", start, end)
            if boundary > start:
                end = boundary + 2
        chunk = text[start:end]
        if chunk:
            chunks.append(chunk)
        start = end
    return tuple(chunks)


def plan_import(config: ImportConfig) -> tuple[Chunk, ...]:
    total = 0
    chunks: list[Chunk] = []
    for source in config.sources:
        for relative in source.files:
            data = _read_source(source.root, relative)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise MemoryImportError("memory import exceeds the total byte limit")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MemoryImportError("source file must be UTF-8 Markdown") from exc
            # Hindsight v0.10.1 strips these characters before hashing. Reject
            # instead of silently changing imported content and its retry identity.
            if any((ord(char) < 32 and char not in "\t\n\r") or ord(char) == 127 for char in text):
                raise MemoryImportError("source contains unsupported control characters")
            source_hash = hashlib.sha256(data).hexdigest()
            for index, content in enumerate(split_markdown(text)):
                chunks.append(Chunk(source.id, source.harness, relative, source_hash, index, content))
    return tuple(chunks)


def _http(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method=method)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler()
    )
    try:
        with opener.open(request, timeout=300) as response:
            body = response.read(256 * 1024 + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, {}
        raise MemoryImportError("Hindsight request was rejected") from None
    except (OSError, urllib.error.URLError, ValueError):
        raise MemoryImportError("Hindsight request failed") from None
    if len(body) > 256 * 1024:
        raise MemoryImportError("Hindsight response exceeded the limit")
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MemoryImportError("Hindsight response was invalid") from None
    if not isinstance(parsed, dict):
        raise MemoryImportError("Hindsight response was invalid")
    return status, parsed


def _document_url(config: ImportConfig, document_id: str) -> str:
    return "%s/v1/default/banks/%s/documents/%s" % (
        config.base_url, urllib.parse.quote(config.bank, safe=""), urllib.parse.quote(document_id, safe=""),
    )


def _matches(chunk: Chunk, body: dict) -> bool:
    metadata = body.get("document_metadata")
    return (isinstance(metadata, dict)
            and metadata.get("source_id") == chunk.source_id
            and metadata.get("harness") == chunk.harness
            and metadata.get("source_file") == chunk.source_file
            and metadata.get("source_sha256") == chunk.source_sha256
            and metadata.get("chunk_index") == str(chunk.index)
            and metadata.get("importer_version") == IMPORTER_VERSION
            and body.get("content_hash") == chunk.content_sha256)


def _summary(config: ImportConfig, chunks: tuple[Chunk, ...]) -> dict:
    by_source = []
    for source in config.sources:
        selected = [chunk for chunk in chunks if chunk.source_id == source.id]
        by_source.append({"id": source.id, "harness": source.harness, "files": len(source.files),
                          "chunks": len(selected), "source_sha256": sorted({chunk.source_sha256 for chunk in selected})})
    return {"bank": config.bank, "sources": by_source, "chunks": len(chunks)}


def import_memories(config_path: str | Path, *, confirm: bool = False, transport=_http) -> dict:
    """Plan an import, or execute it only after a verified defense check."""
    config = load_config(config_path)
    chunks = plan_import(config)
    report = _summary(config, chunks)
    if not confirm:
        return {"apply": False, "changed": False, **report}
    try:
        token_bytes = read_private_auth_file(config.auth_file, max_bytes=4096)
    except AuthFileError:
        raise MemoryImportError("auth_file is not a usable private credential") from None
    if not token_bytes or any(byte < 0x21 or byte > 0x7E for byte in token_bytes):
        raise MemoryImportError("auth_file is not a usable private credential")
    token = token_bytes.decode("ascii")
    status, policy = transport("GET", "%s/v1/default/banks/%s/config" % (config.base_url, urllib.parse.quote(config.bank, safe="")), token)
    if (status != 200 or not isinstance(policy, dict)
            or not isinstance(policy.get("config"), dict)
            or policy["config"].get("memory_defense") != _DEFENSE):
        raise MemoryImportError("target bank does not enforce block_sensitive_data defense")
    imported, skipped, failed = 0, 0, []
    for chunk in chunks:
        try:
            status, existing = transport("GET", _document_url(config, chunk.document_id), token)
            if status == 200:
                if not isinstance(existing, dict) or not _matches(chunk, existing):
                    raise MemoryImportError("existing document provenance does not match")
                skipped += 1
                continue
            if status != 404:
                raise MemoryImportError("Hindsight document lookup failed")
            payload = {"async": False, "items": [{"content": chunk.content, "document_id": chunk.document_id,
                       "metadata": {"source_id": chunk.source_id, "harness": chunk.harness,
                                    "source_file": chunk.source_file, "source_sha256": chunk.source_sha256,
                                    "chunk_index": str(chunk.index), "importer_version": IMPORTER_VERSION},
                       "tags": ["memory-import", chunk.harness]}]}
            status, response = transport("POST", "%s/v1/default/banks/%s/memories" % (config.base_url, urllib.parse.quote(config.bank, safe="")), token, payload)
            if status != 200 or not isinstance(response, dict) or response.get("success") is not True:
                raise MemoryImportError("Hindsight rejected a memory chunk")
            imported += 1
        except MemoryImportError:
            failed.append(chunk.document_id)
            # An interrupted synchronous retain can still be running upstream.
            # Stop here rather than accumulating requests behind an uncertain write.
            break
    return {"apply": True, "changed": bool(imported), "imported": imported, "skipped": skipped,
            "failed_chunks": failed, **report}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving harness memory-import")
    parser.add_argument("--config", required=True, help="Exact TOML import configuration.")
    parser.add_argument("--confirm", action="store_true", help="Import after defense and idempotency checks.")
    return parser


def dispatch(argv=None) -> CommandResult:
    args = _parser().parse_args(argv)
    try:
        data = import_memories(args.config, confirm=args.confirm)
    except MemoryImportError as exc:
        error = SafetyError(str(exc), code="memory_import_refused")
        return CommandResult(error=error, human_stderr="anvil-serving harness memory-import: %s\n" % exc)
    if data.get("failed_chunks"):
        error = SafetyError("one or more memory chunks were rejected", code="memory_import_partial")
        return CommandResult(data=data, error=error, human_stdout=json.dumps(data, sort_keys=True) + "\n")
    return CommandResult(data=data, human_stdout=json.dumps(data, sort_keys=True) + "\n")


def main(argv=None) -> int:
    result = dispatch(argv)
    if result.human_stdout:
        sys.stdout.write(result.human_stdout)
    if result.human_stderr:
        sys.stderr.write(result.human_stderr)
    return result.exit_code
