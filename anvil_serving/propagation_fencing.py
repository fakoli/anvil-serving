"""Owner-custody fencing and durable journals for native propagation writes.

This module deliberately does not launch jobs or choose files.  A native owner
constructs a sealed grant from an admitted canonical contract, then the existing
catalog/media transactions execute inside this guard.  The lock identity is the
owner's canonical user/resource context; API config, home, and backup arguments
can only match a grant's pre-bound target set.  The on-disk journal survives a
dead process and blocks recovery until an owner reconciles partial effects.

No public entry point infers this owner context.  Until T010 enrolls legacy
writers, calls without a trusted fence retain their existing behavior; previews
never acquire this lock or create a reservation.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import errno
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Callable, Iterable, Iterator, Mapping
from types import MappingProxyType

from .control_plane.propagation import parse_contract, _EFFECTS
from .control_plane import bootstrap_shim
from .control_plane.mcp import auth_file


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "anvil-serving.propagation-native-journal/v1"
_LEGACY_CATALOG_ROOT: ContextVar[Path | None] = ContextVar("legacy_catalog_root", default=None)


class PropagationFenceError(ValueError):
    """A stable failure code for an owner-controlled native mutation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _token(value: object) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise PropagationFenceError("malformed_owner_context")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise PropagationFenceError("malformed_grant")
    return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


_UNCHECKED = object()


class _HeldFiles:
    """Native directory custody for one lock lifetime; no pathname-only writes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directories: dict[Path, int] = {}

    def close(self) -> None:
        try:
            for descriptor in reversed(self.directories.values()):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        finally:
            self.directories.clear()

    def check(self) -> None:
        # Windows handles deny directory deletion/rename. POSIX handles anchor
        # relative writes, and this check detects detached/replaced namespaces.
        if os.name != "nt":
            for path, descriptor in self.directories.items():
                parent = None if path == path.parent else self.directories.get(path.parent)
                info = os.stat(path.name if parent is not None else path,
                               dir_fd=parent, follow_symlinks=False)
                if not os.path.samestat(info, os.fstat(descriptor)):
                    raise PropagationFenceError("unsafe_custody_path")

    @staticmethod
    def _permissions(descriptor: int, *, directory: bool, private: bool) -> None:
        try:
            if os.name == "nt":
                is_dir, _identity, links = bootstrap_shim._windows_handle_details_from_descriptor(descriptor)
                if is_dir != directory or (not directory and links != 1):
                    raise PropagationFenceError("unsafe_custody_path")
                if private:
                    auth_file._require_windows_private_descriptor(descriptor)
                elif bootstrap_shim.inspect_opened_permissions(descriptor, ancestor=directory).name not in {
                    "OWNER_READONLY", "OWNER_WRITABLE",
                }:
                    raise PropagationFenceError("unsafe_custody_path")
            else:
                info = os.fstat(descriptor)
                if directory:
                    auth_file._require_posix_private_ancestor(descriptor)
                    if private and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
                        raise PropagationFenceError("unsafe_journal_root")
                elif private:
                    auth_file._require_posix_private_descriptor(descriptor)
                elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                      or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022):
                    raise PropagationFenceError("unsafe_custody_path")
                if not directory:
                    auth_file._require_macos_no_extended_acl(descriptor)
        except (OSError, auth_file.AuthFileError, bootstrap_shim._UnsafeObject) as exc:
            raise PropagationFenceError("unsafe_custody_path") from exc

    def directory(self, path: Path, *, create: bool = False, private: bool = False) -> int:
        self.check()
        if path not in self.directories:
            if path == path.parent:
                descriptor = (bootstrap_shim._windows_open_prefix(str(path), directory=True)
                              if os.name == "nt" else os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            else:
                parent = self.directory(path.parent, create=create)
                if create and path.is_relative_to(self.root):
                    try:
                        if os.name == "nt":
                            path.mkdir(mode=0o777)
                        else:
                            os.mkdir(path.name, mode=0o700, dir_fd=parent)
                            os.fsync(parent)
                    except FileExistsError:
                        pass
                descriptor = (bootstrap_shim._windows_open_prefix(str(path), directory=True)
                              if os.name == "nt" else os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent))
            try:
                self._permissions(descriptor, directory=True, private=private)
            except BaseException:
                os.close(descriptor)
                raise
            self.directories[path] = descriptor
        descriptor = self.directories[path]
        self._permissions(descriptor, directory=True, private=private)
        return descriptor

    def read(self, path: Path, *, private: bool = False) -> bytes | None:
        if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(self.root):
            raise PropagationFenceError("untrusted_target_path")
        try:
            parent = self.directory(path.parent)
        except (OSError, auth_file.AuthFileError) as exc:
            raise PropagationFenceError("unsafe_custody_path") from exc
        try:
            descriptor = (bootstrap_shim._windows_open_prefix(str(path), directory=False)
                          if os.name == "nt" else os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent))
        except FileNotFoundError:
            return None
        except OSError as exc:
            # CreateFileW's existing helper reports an untyped OSError. Missing
            # leaves are allowed only while their parent remains held/validated.
            if os.name == "nt":
                try:
                    os.lstat(path)
                except FileNotFoundError:
                    self.check()
                    return None
            raise PropagationFenceError("unsafe_custody_path") from exc
        try:
            self._permissions(descriptor, directory=False, private=private)
            # ponytail: bound local documents/journals to 2 MiB; split the
            # permanent index if actual lifetime volume reaches this ceiling.
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                content = stream.read(2 * 1024 * 1024 + 1)
            if len(content) > 2 * 1024 * 1024:
                raise PropagationFenceError("document_too_large")
            self.check()
            return content
        finally:
            os.close(descriptor)

    def mode(self, path: Path) -> int:
        if os.name == "nt":
            return 0o600  # Windows custody is an ACL contract, not POSIX mode bits.
        if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(self.root):
            raise PropagationFenceError("untrusted_target_path")
        parent = self.directory(path.parent)
        info = os.stat(path if os.name == "nt" else path.name, follow_symlinks=False,
                       **({} if os.name == "nt" else {"dir_fd": parent}))
        if not stat.S_ISREG(info.st_mode):
            raise PropagationFenceError("unsafe_custody_path")
        return stat.S_IMODE(info.st_mode)

    def write(self, path: Path, data: bytes, *, mode: int = 0o600,
              private: bool = False, expected: bytes | None | object = _UNCHECKED,
              pre_replace: Callable[[], None] | None = None) -> None:
        if len(data) > 2 * 1024 * 1024:
            raise PropagationFenceError("document_too_large")
        if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(self.root):
            raise PropagationFenceError("untrusted_target_path")
        parent = self.directory(path.parent, private=private)
        if expected is not _UNCHECKED and self.read(path) != expected:
            raise PropagationFenceError("external_drift")
        temporary = ".propagation-" + secrets.token_hex(12)
        descriptor = os.open(path.parent / temporary if os.name == "nt" else temporary,
                             os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_BINARY", 0), 0o600,
                             **({} if os.name == "nt" else {"dir_fd": parent}))
        try:
            self._permissions(descriptor, directory=False, private=True)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(data)
                stream.flush()
                if os.name != "nt":
                    if hasattr(os, "fchmod"):
                        os.fchmod(stream.fileno(), mode)
                    else:
                        os.chmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            self.check()
            if expected is not _UNCHECKED and self.read(path) != expected:
                raise PropagationFenceError("external_drift")
            if pre_replace is not None:
                pre_replace()
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
                move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
                move.restype = wintypes.BOOL
                if not move(str(path.parent / temporary), str(path), 0x1 | 0x8):
                    raise OSError(ctypes.get_last_error(), "native replacement failed")
            else:
                os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            if self.read(path, private=private) != data:
                raise PropagationFenceError("external_drift")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(path.parent / temporary if os.name == "nt" else temporary,
                          **({} if os.name == "nt" else {"dir_fd": parent}))
            except FileNotFoundError:
                pass


@dataclass(frozen=True, slots=True)
class TrustedNativeOwner:
    """Private bootstrap data; public callers must not derive it from paths."""

    owner_id: str
    resource_id: str
    storage_root: Path
    epoch: str = "epoch-1"
    backup_root: Path | None = None
    catalog_digest: str | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc), repr=False, compare=False)
    effect_bindings: Mapping[str, tuple[str, Path]] = field(default_factory=dict, repr=False)
    current_authority: Callable[[str, int, str], bool] | None = field(default=None, repr=False, compare=False)
    recovery_quiescent: Callable[[str, str, int], bool] | None = field(default=None, repr=False, compare=False)
    recovery_authority: Callable[[str, str, str, str, str], bool] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _token(self.owner_id)
        _token(self.resource_id)
        _token(self.epoch)
        if self.catalog_digest is not None:
            _digest(self.catalog_digest)
        if not callable(self.clock):
            raise PropagationFenceError("malformed_owner_context")
        root = Path(self.storage_root).absolute()
        if ".." in root.parts or (os.name == "nt" and root.drive.startswith("\\\\")):
            raise PropagationFenceError("malformed_owner_context")
        backup = root / ".config" / "anvil-serving" / "backups" / "propagation" if self.backup_root is None else Path(self.backup_root).absolute()
        if ".." in backup.parts or not backup.is_relative_to(root):
            raise PropagationFenceError("malformed_owner_context")
        if not isinstance(self.effect_bindings, Mapping):
            raise PropagationFenceError("malformed_owner_context")
        bindings = {}
        for effect, binding in self.effect_bindings.items():
            _token(effect)
            if type(binding) is not tuple or len(binding) != 2 or type(binding[0]) is not str or binding[0] not in _EFFECTS:
                raise PropagationFenceError("malformed_owner_context")
            path = Path(binding[1]).absolute()
            if ".." in path.parts or not path.is_relative_to(root) or path == root:
                raise PropagationFenceError("untrusted_target_path")
            bindings[effect] = (binding[0], path)
        object.__setattr__(self, "effect_bindings", MappingProxyType(bindings))
        object.__setattr__(self, "storage_root", root)
        object.__setattr__(self, "backup_root", backup)


@dataclass(frozen=True, slots=True)
class NativeMutationGrant:
    """In-process sealed authority resolved by the authenticated native owner."""

    owner_id: str
    resource_id: str
    epoch: str
    contract_digest: str
    generation: int
    effects: tuple[str, ...]
    effect_targets: tuple[tuple[str, str], ...]
    targets_digest: str
    catalog_digest: str
    backup_root: str
    _seal: object = field(repr=False, compare=False)


class NativeMutationJournal:
    """Durably records original and observed bytes before each native effect."""

    def __init__(self, fence: "NativeMutationFence", state: dict, path: Path) -> None:
        self._fence = fence
        self._state = state
        self._path = path

    @property
    def reservation_id(self) -> str:
        return self._state["reservation_id"]

    @property
    def catalog_digest(self) -> str:
        return self._state["catalog_digest"]

    @property
    def contract_digest(self) -> str:
        return self._state["contract_digest"]

    @property
    def generation(self) -> int:
        return self._state["generation"]

    def backup(self, paths: Iterable[str | Path]) -> Path:
        """Capture verified original bytes using the owner's held file custody."""
        from .client_catalog_sync import _backup

        selected = tuple(self._fence._trusted_path(path) for path in paths)
        if not selected or len(set(selected)) != len(selected):
            raise PropagationFenceError("malformed_effect")
        if any(str(path) not in self._state["effect_targets"].values() for path in selected):
            raise PropagationFenceError("effect_not_granted")
        return _backup(list(selected), self._fence.owner.backup_root,
                       self.contract_digest, journal=self)

    @property
    def started_effects(self) -> tuple[str, ...]:
        return tuple(sorted(self._state["effects"]))

    def effect_identity(self, effect_id: str) -> tuple[Path, str | None]:
        """Read the durable target and desired digest for restart reconciliation."""
        effect = self._effect(effect_id)
        return Path(effect["path"]), effect["desired_digest"]

    def backup_bound(self, effect_id: str) -> bool:
        return self._effect(effect_id)["backup_id"] is not None

    def is_verified(self, effect_id: str) -> bool:
        return self._effect(effect_id)["state"] == "verified"

    def begin_effect(
        self,
        effect_id: str,
        path: str | Path,
        before: bytes | None,
        desired: bytes | None,
    ) -> None:
        """Record an exact raw before image before a mutation can begin."""
        effect_id = _token(effect_id)
        self._fence._require_effect_authority(self._state)
        selected = self._fence._trusted_path(path)
        if (effect_id not in self._state["allowed_effects"]
                or self._state["effect_targets"].get(effect_id) != str(selected)):
            raise PropagationFenceError("effect_not_granted")
        if desired is not None and not isinstance(desired, bytes):
            raise PropagationFenceError("malformed_effect")
        effects = self._state["effects"]
        existing = effects.get(effect_id)
        original_digest = None if before is None else _sha256(before)
        desired_digest = None if desired is None else _sha256(desired)
        if existing is not None:
            if (existing.get("path") != str(selected)
                    or existing.get("before_digest") != original_digest
                    or existing.get("desired_digest") != desired_digest):
                raise PropagationFenceError("effect_identity_conflict")
            return
        effect = {
            "path": str(selected),
            "before_digest": original_digest,
            "desired_digest": desired_digest,
            "backup_id": None,
            "state": "prepared",
            "observed_digest": None,
        }
        effects[effect_id] = effect
        self._save()

    def bind_backup(self, effect_id: str, backup_path: str | Path) -> None:
        """Bind a verified manifest entry to this exact durable before image."""
        effect = self._effect(effect_id)
        bundle = self._fence._trusted_path(backup_path)
        if bundle.parent != self._fence.owner.backup_root:
            raise PropagationFenceError("backup_root_mismatch")
        try:
            raw_manifest = self._fence._held().read(bundle / "manifest.json", private=True)
            manifest = json.loads(raw_manifest)
            entries = manifest["files"]
            matches = [item for item in entries if item.get("source") == effect["path"]]
            if len(matches) != 1:
                raise ValueError
            entry = matches[0]
            if entry.get("existed"):
                name = entry["backup"]
                if type(name) is not str or Path(name).name != name:
                    raise ValueError
                stored = self._fence._held().read(bundle / name, private=True)
                if (_sha256(stored) != entry.get("sha256")
                        or entry.get("sha256") != effect["before_digest"]):
                    raise ValueError
            elif effect["before_digest"] is not None:
                raise ValueError
        except (OSError, TypeError, KeyError, StopIteration, ValueError, json.JSONDecodeError) as exc:
            raise PropagationFenceError("backup_binding_mismatch") from exc
        binding = {"backup_id": _token(bundle.name), "backup_path": str(bundle),
                   "backup_manifest_digest": _sha256(raw_manifest)}
        if effect["backup_id"] is not None and any(effect.get(k) != v for k, v in binding.items()):
            raise PropagationFenceError("backup_identity_conflict")
        effect.update(binding)
        self._save()

    def require_before_bytes(self, effect_id: str, observed: bytes | None) -> None:
        effect = self._effect(effect_id)
        self._fence._require_effect_authority(self._state)
        actual = self.read(Path(effect["path"]))
        if actual != observed:
            raise PropagationFenceError("external_drift")
        digest = None if observed is None else _sha256(observed)
        if digest != effect["before_digest"]:
            effect["observed_digest"] = digest
            effect["state"] = "drift"
            self._save()
            raise PropagationFenceError("external_drift")

    def read(self, path: str | Path) -> bytes | None:
        return self._fence._held().read(self._fence._trusted_path(path))

    @property
    def files(self) -> _HeldFiles:
        """The same custody used by the existing native backup transaction."""
        return self._fence._held()

    def write(self, effect_id: str, data: bytes, *, mode: int = 0o600) -> None:
        effect = self._effect(effect_id)
        self._fence._require_effect_authority(self._state)
        before = self.read(effect["path"])
        self.require_before_bytes(effect_id, before)
        if effect["backup_id"] is None:
            raise PropagationFenceError("backup_required")
        self.bind_backup(effect_id, effect["backup_path"])
        if effect["desired_digest"] != _sha256(data):
            raise PropagationFenceError("effect_identity_conflict")
        self._fence._held().write(Path(effect["path"]), data, mode=mode, expected=before,
                                  pre_replace=lambda: self._fence._require_effect_authority(self._state))

    def observe_bytes(self, effect_id: str, observed: bytes | None) -> None:
        effect = self._effect(effect_id)
        if self.read(effect["path"]) != observed:
            raise PropagationFenceError("external_drift")
        digest = None if observed is None else _sha256(observed)
        effect["observed_digest"] = digest
        effect["state"] = "verified" if digest == effect["desired_digest"] else "drift"
        self._save()

    def mark_uncertain(self, effect_id: str) -> None:
        effect = self._effect(effect_id)
        effect["state"] = "uncertain"
        self._save()

    def _save(self) -> None:
        self._fence._write_state(self._path, self._state)

    def _effect(self, effect_id: str) -> dict:
        effect = self._state["effects"].get(_token(effect_id))
        if not isinstance(effect, dict):
            raise PropagationFenceError("effect_not_started")
        return effect


class NativeMutationFence:
    """One canonical owner/resource lock and permanent operation journals."""

    def __init__(self, owner: TrustedNativeOwner, journal_root: str | Path) -> None:
        if not isinstance(owner, TrustedNativeOwner):
            raise PropagationFenceError("malformed_owner_context")
        # Do not permit a call-specific directory to split custody.
        self.owner = owner
        self.journal_root = owner.storage_root / ".config" / "anvil-serving" / "propagation-fencing"
        self._seal = object()
        self._files: ContextVar[_HeldFiles | None] = ContextVar("propagation_files", default=None)

    @property
    def _catalog_cutover_path(self) -> Path:
        return self.journal_root / "client-catalog.cutover.json"

    def activate_catalog_cutover(self, canonical_contract: bytes) -> None:
        """An admitted owner retires legacy client writers before the first effect."""
        if self.owner.resource_id != "client-catalog":
            raise PropagationFenceError("resource_mismatch")
        try:
            contract = parse_contract(canonical_contract)
        except (TypeError, ValueError) as exc:
            raise PropagationFenceError("malformed_grant") from exc
        if contract.canonical != canonical_contract:
            raise PropagationFenceError("noncanonical_contract")
        if not any("client-catalog" in target["resource_keys"] and "catalog-apply" in target["effects"]
                   for target in contract.value["targets"]):
            raise PropagationFenceError("effect_not_approved")
        generation = contract.value["generation"]
        digest = contract.digest
        value = {"schema": "anvil-serving.catalog-cutover/v1", "contract_digest": digest,
                 "generation": generation, "epoch": self.owner.epoch}
        with self._installed_catalog_lock(), self._lock():
            self._require_current_authority(contract.value)
            self._check_authority(digest, generation)
            index = self._read_index()
            if any(row["status"] != "completed" for row in index["operations"].values()):
                raise PropagationFenceError("unresolved_reservation")
            existing = self._held().read(self._catalog_cutover_path, private=True)
            if existing is not None:
                current = self._read_catalog_cutover()
                if current == value:
                    return
                if generation <= current["generation"]:
                    raise PropagationFenceError("stale_generation")
            if generation <= index["high_water_generation"]:
                raise PropagationFenceError("stale_generation")
            self._write_json(self._catalog_cutover_path, value)

    def _read_catalog_cutover(self) -> dict | None:
        raw = self._held().read(self._catalog_cutover_path, private=True)
        if raw is None:
            return None
        value = self._read_json(self._catalog_cutover_path)
        if (set(value) != {"schema", "contract_digest", "generation", "epoch"}
                or value["schema"] != "anvil-serving.catalog-cutover/v1"
                or type(value["generation"]) is not int or value["generation"] < 1):
            raise PropagationFenceError("unsafe_journal")
        _digest(value["contract_digest"])
        _token(value["epoch"])
        return value

    @classmethod
    @contextmanager
    def legacy_catalog_write(cls, storage_root: str | Path, *, catalog_lock_fd: int | None = None) -> Iterator[None]:
        """Serialize existing direct writers with cutover; refuse once retired."""
        root = Path(storage_root).absolute()
        held = _LEGACY_CATALOG_ROOT.get()
        if held is not None:
            if held != root:
                raise PropagationFenceError("resource_mismatch")
            yield
            return
        owner = TrustedNativeOwner("legacy-client-catalog", "client-catalog", root)
        fence = cls(owner, root)
        with fence._installed_catalog_lock(borrowed_fd=catalog_lock_fd, create=True), fence._lock():
            if fence._read_catalog_cutover() is not None:
                raise PropagationFenceError("stale_generation")
            token = _LEGACY_CATALOG_ROOT.set(root)
            try:
                yield
            finally:
                _LEGACY_CATALOG_ROOT.reset(token)

    def grant(
        self, *, canonical_contract: bytes, generation: int, effects: Iterable[str],
        target_paths: Iterable[str | Path], effect_targets: Mapping[str, str | Path] | None = None,
    ) -> NativeMutationGrant:
        if not isinstance(canonical_contract, bytes):
            raise PropagationFenceError("malformed_grant")
        try:
            contract = parse_contract(canonical_contract)
        except (TypeError, ValueError) as exc:
            raise PropagationFenceError("malformed_grant") from exc
        if contract.canonical != canonical_contract:
            raise PropagationFenceError("noncanonical_contract")
        if type(generation) is not int or isinstance(generation, bool) or generation != contract.value["generation"]:
            raise PropagationFenceError("grant_generation_mismatch")
        allowed = tuple(sorted({_token(effect) for effect in effects}))
        targets = tuple(sorted(str(self._trusted_path(path)) for path in target_paths))
        if not allowed or not targets:
            raise PropagationFenceError("malformed_grant")
        if effect_targets is None:
            if len(allowed) != 1 or len(targets) != 1:
                raise PropagationFenceError("effect_target_mapping_required")
            mapped = ((allowed[0], targets[0]),)
        else:
            mapped = tuple(sorted((_token(effect), str(self._trusted_path(path))) for effect, path in effect_targets.items()))
            if tuple(effect for effect, _ in mapped) != allowed or any(path not in targets for _, path in mapped):
                raise PropagationFenceError("grant_target_mismatch")
        approved_targets = [
            target for target in contract.value["targets"]
            if self.owner.resource_id in target["resource_keys"]
        ]
        declared = {effect for target in approved_targets for effect in target["effects"]}
        # Installation owns these bindings. Requests cannot choose a new effect,
        # relabel one permission as another, or widen a resource to its whole home.
        bindings = self.owner.effect_bindings
        if not approved_targets or any(effect not in bindings or bindings[effect][0] not in declared for effect in allowed):
            raise PropagationFenceError("effect_not_approved")
        expected = tuple((effect, str(bindings[effect][1])) for effect in allowed)
        if mapped != expected or set(targets) != {path for _, path in expected}:
            raise PropagationFenceError("grant_target_mismatch")
        catalog_digest = contract.value["inputs"]["catalog_digest"]
        if self.owner.catalog_digest is not None and self.owner.catalog_digest != catalog_digest:
            raise PropagationFenceError("catalog_expectation_mismatch")
        return NativeMutationGrant(self.owner.owner_id, self.owner.resource_id, self.owner.epoch,
            contract.digest, generation, allowed, mapped, _sha256(_canonical(targets)), catalog_digest, str(self.owner.backup_root), self._seal)

    @contextmanager
    def preview(self, grant: NativeMutationGrant, *, canonical_contract: bytes,
                target_paths: Iterable[str | Path]) -> Iterator[object]:
        """Read client documents under native directory custody without reserving a generation."""
        from types import SimpleNamespace
        targets = tuple(sorted(str(self._trusted_path(path)) for path in target_paths))
        with self._lock():
            self._validate_grant(grant, canonical_contract, targets)
            files = self._held()
            yield SimpleNamespace(read=lambda path: files.read(self._trusted_path(path)),
                                  files=files, catalog_digest=grant.catalog_digest)

    @contextmanager
    def transaction(self, grant: NativeMutationGrant, *, canonical_contract: bytes,
                    target_paths: Iterable[str | Path]) -> Iterator[NativeMutationJournal]:
        if not isinstance(canonical_contract, bytes):
            raise PropagationFenceError("malformed_grant")
        targets = tuple(sorted(str(self._trusted_path(path)) for path in target_paths))
        if self.owner.resource_id == "client-catalog":
            # Validate before retiring legacy writers. Activation takes the
            # installed Pi lock first, so it cannot run inside _lock().
            with self._lock():
                self._validate_grant(grant, canonical_contract, targets)
            self.activate_catalog_cutover(canonical_contract)
        with self._lock():
            self._validate_grant(grant, canonical_contract, targets)
            if self.owner.resource_id == "client-catalog":
                cutover = self._read_catalog_cutover()
                if cutover is None or (cutover["contract_digest"] != grant.contract_digest
                                   or cutover["generation"] != grant.generation
                                   or cutover["epoch"] != grant.epoch):
                    raise PropagationFenceError("stale_generation")
            for target in targets:
                self._held().read(Path(target))
            reservation_id = self._reservation_id(grant)
            index = self._read_index()
            self._validate_generation(index, grant, reservation_id)
            path = self._operation_path(reservation_id)
            state = {
                "schema": _SCHEMA, "owner_id": self.owner.owner_id, "resource_id": self.owner.resource_id,
                "epoch": grant.epoch, "contract_digest": grant.contract_digest, "generation": grant.generation,
                "catalog_digest": grant.catalog_digest, "deadline_at": self._contract_deadline(canonical_contract).isoformat().replace("+00:00", "Z"), "effect_targets": dict(grant.effect_targets),
                "targets_digest": grant.targets_digest, "allowed_effects": list(grant.effects),
                "reservation_id": reservation_id, "status": "active", "effects": {}, "updated_at": self._now(),
            }
            # Index first: a crash before the operation body exists fails closed as
            # unresolved rather than allowing a later generation to reuse it.
            index["high_water_generation"] = max(index["high_water_generation"], grant.generation)
            index["latest_reservation_id"] = reservation_id
            index["operations"][reservation_id] = {"generation": grant.generation, "status": "active"}
            self._write_index(index)
            self._write_state(path, state)
            journal = NativeMutationJournal(self, state, path)
            try:
                yield journal
            except BaseException:
                state["status"] = "recovery_required"
                self._write_state(path, state)
                self._set_operation_status(index, reservation_id, "recovery_required")
                raise
            if any(effect["state"] != "verified" for effect in state["effects"].values()):
                state["status"] = "recovery_required"
                self._write_state(path, state)
                self._set_operation_status(index, reservation_id, "recovery_required")
                raise PropagationFenceError("recovery_required")
            state["status"] = "completed"
            self._write_state(path, state)
            self._set_operation_status(index, reservation_id, "completed")

    def journal(self, reservation_id: str | None = None) -> dict | None:
        """Return a copy of a permanent operation journal without clearing it."""
        with self._lock():
            index = self._read_index()
            chosen = reservation_id or index["latest_reservation_id"]
            if chosen is None:
                return None
            _token(chosen)
            if chosen not in index["operations"]:
                return None
            state = self._read_state(self._operation_path(chosen))
            return json.loads(_canonical(state).decode("ascii"))

    @contextmanager
    def resume(self, grant: NativeMutationGrant, *, canonical_contract: bytes,
               target_paths: Iterable[str | Path]) -> Iterator[NativeMutationJournal]:
        """Reconcile the original reservation; never infer that a process stopped.

        Installed owner custody must prove the original execution quiescent.
        Exact desired bytes can be acknowledged after expiry. Every new/retried
        write still checks live authority, deadline and the original backup.
        """
        targets = tuple(sorted(str(self._trusted_path(path)) for path in target_paths))
        with self._lock():
            self._validate_grant(grant, canonical_contract, targets, require_fresh=False)
            if self.owner.resource_id == "client-catalog":
                cutover = self._read_catalog_cutover()
                if cutover is None or (cutover["contract_digest"] != grant.contract_digest
                                   or cutover["generation"] != grant.generation
                                   or cutover["epoch"] != grant.epoch):
                    raise PropagationFenceError("stale_generation")
            reservation_id = self._reservation_id(grant)
            index = self._read_index()
            row = index["operations"].get(reservation_id)
            if row is None or row["generation"] != grant.generation:
                raise PropagationFenceError("unresolved_reservation")
            if row["status"] == "completed":
                raise PropagationFenceError("already_completed")
            if index["latest_reservation_id"] != reservation_id or index["high_water_generation"] != grant.generation:
                raise PropagationFenceError("stale_generation")
            try:
                quiescent = self.owner.recovery_quiescent(reservation_id, grant.contract_digest, grant.generation)
            except Exception:
                quiescent = False
            if quiescent is not True:
                raise PropagationFenceError("quiescence_unproven")
            path = self._operation_path(reservation_id)
            state = self._read_state(path)
            expected = {"reservation_id": reservation_id, "contract_digest": grant.contract_digest,
                        "generation": grant.generation, "catalog_digest": grant.catalog_digest,
                        "targets_digest": grant.targets_digest, "allowed_effects": list(grant.effects),
                        "effect_targets": dict(grant.effect_targets),
                        "deadline_at": self._contract_deadline(canonical_contract).isoformat().replace("+00:00", "Z")}
            if any(state[key] != value for key, value in expected.items()):
                raise PropagationFenceError("grant_binding_mismatch")
            journal = NativeMutationJournal(self, state, path)
            try:
                for effect_id, effect in state["effects"].items():
                    if (effect_id not in grant.effects or type(effect) is not dict
                            or effect.get("path") != dict(grant.effect_targets)[effect_id]
                            or effect.get("state") not in {"prepared", "verified", "drift", "uncertain"}
                            or any(effect.get(key) is not None and (type(effect[key]) is not str or _DIGEST.fullmatch(effect[key]) is None)
                                   for key in ("before_digest", "desired_digest", "observed_digest"))):
                        raise PropagationFenceError("unsafe_journal")
                    observed = journal.read(effect["path"])
                    actual = None if observed is None else _sha256(observed)
                    if not effect.get("backup_id") or not effect.get("backup_path"):
                        # A crash while recording effects or binding backups is
                        # safe to resume only if no effect could have begun.
                        if (effect["state"] != "prepared" or effect["observed_digest"] is not None
                                or actual != effect["before_digest"]):
                            raise PropagationFenceError("backup_required")
                        continue
                    journal.bind_backup(effect_id, effect["backup_path"])
                    if actual == effect["desired_digest"]:
                        journal.observe_bytes(effect_id, observed)
                    elif actual != effect["before_digest"]:
                        raise PropagationFenceError("external_drift")
                    else:
                        effect["state"] = "prepared"
                        effect["observed_digest"] = actual
                        journal._save()
                yield journal
                for effect_id, effect in state["effects"].items():
                    journal.observe_bytes(effect_id, journal.read(effect["path"]))
                if (set(state["effects"]) != set(grant.effects)
                        or any(effect["state"] != "verified" for effect in state["effects"].values())):
                    raise PropagationFenceError("recovery_required")
            except BaseException:
                state["status"] = "recovery_required"
                self._write_state(path, state)
                self._set_operation_status(index, reservation_id, "recovery_required")
                raise
            state["status"] = "completed"
            self._write_state(path, state)
            self._set_operation_status(index, reservation_id, "completed")

    def lookup(self, grant: NativeMutationGrant) -> dict | None:
        """Resolve an exact completed identity without allocating a replacement."""
        if not isinstance(grant, NativeMutationGrant) or grant._seal is not self._seal:
            raise PropagationFenceError("untrusted_grant")
        reservation_id = self._reservation_id(grant)
        return self.journal(reservation_id)

    def advance_recovery_epoch(self, new_epoch: str, approval_digest: str, target_set_digest: str) -> str:
        """Fence restored work after owner-approved, quiescent local reconciliation.

        The caller's protected recovery authority must bind the whole target set.
        This local transition alone never grants a new mutation.
        """
        _token(new_epoch)
        _digest(approval_digest)
        _digest(target_set_digest)
        if new_epoch == self.owner.epoch:
            raise PropagationFenceError("recovery_epoch_unchanged")
        if not callable(self.owner.recovery_quiescent) or not callable(self.owner.recovery_authority):
            raise PropagationFenceError("recovery_authority_unavailable")
        with self._lock():
            if self._held().read(self._index_path, private=True) is None:
                raise PropagationFenceError("unsafe_journal")
            index = self._read_index()
            parent = self._held().directory(self.journal_root, private=True)
            prefix = self.owner.resource_id + "."
            cutover_digest = None
            cutover_epoch = None
            if self.owner.resource_id == "client-catalog":
                marker = self._held().read(self._catalog_cutover_path, private=True)
                if marker is not None:
                    cutover = self._read_catalog_cutover()
                    cutover_digest = _sha256(marker)
                    cutover_epoch = cutover["epoch"]
                    if cutover_epoch == self.owner.epoch:
                        matching = [reservation for reservation, row in index["operations"].items()
                            if row["status"] == "completed" and row["generation"] == cutover["generation"]
                            and self._read_state(self._operation_path(reservation))["contract_digest"] == cutover["contract_digest"]]
                        if len(matching) != 1:
                            raise PropagationFenceError("unindexed_journal")
                    elif (index.get("recovery", {}).get("cutover_epoch") != cutover_epoch
                          or index["recovery"].get("cutover_digest") != cutover_digest):
                        raise PropagationFenceError("unindexed_journal")
                elif index["operations"] or index.get("recovery", {}).get("cutover_digest"):
                    raise PropagationFenceError("unindexed_journal")
            for name in os.listdir(self.journal_root if os.name == "nt" else parent):
                if (not name.startswith(prefix) or not name.endswith(".json")
                        or name == prefix + "index.json"
                        or (name == "client-catalog.cutover.json" and cutover_digest is not None)):
                    continue
                reservation = name[len(prefix):-5]
                if reservation in index["operations"]:
                    continue
                orphan = self._read_json(self.journal_root / name)
                if orphan.get("epoch") == self.owner.epoch or "recovery" not in index:
                    raise PropagationFenceError("unindexed_journal")
            expected: dict[str, str] = {}
            for reservation, row in sorted(index["operations"].items(), key=lambda item: item[1]["generation"]):
                if row["status"] != "completed":
                    raise PropagationFenceError("unresolved_reservation")
                state = self._read_state(self._operation_path(reservation))
                if (state["reservation_id"] != reservation or state["generation"] != row["generation"]
                        or state["status"] != "completed"
                        or set(state["effects"]) != set(state["allowed_effects"])):
                    raise PropagationFenceError("unsafe_journal")
                try:
                    quiescent = self.owner.recovery_quiescent(reservation, state["contract_digest"], state["generation"])
                except Exception:
                    quiescent = False
                if quiescent is not True:
                    raise PropagationFenceError("quiescence_unproven")
                for effect_id, effect in state["effects"].items():
                    if (effect.get("state") != "verified" or effect.get("path") != state["effect_targets"].get(effect_id)
                            or not isinstance(effect.get("observed_digest"), str)):
                        raise PropagationFenceError("unsafe_journal")
                    _digest(effect["observed_digest"])
                    expected[effect["path"]] = effect["observed_digest"]
            for name, digest in expected.items():
                observed = self._held().read(self._trusted_path(name))
                if observed is None or _sha256(observed) != digest:
                    raise PropagationFenceError("external_drift")
            evidence = {"old_epoch": self.owner.epoch, "new_epoch": new_epoch,
                "approval_digest": approval_digest, "target_set_digest": target_set_digest,
                "high_water_generation": index["high_water_generation"], "observed": expected}
            if cutover_digest is not None:
                evidence["cutover_digest"] = cutover_digest
            evidence_digest = _sha256(_canonical(evidence))
            try:
                approved = self.owner.recovery_authority(self.owner.epoch, new_epoch, approval_digest,
                                                          target_set_digest, evidence_digest)
            except Exception:
                approved = False
            if approved is not True:
                raise PropagationFenceError("recovery_not_approved")
            recovery = {
                "old_epoch": self.owner.epoch, "approval_digest": approval_digest,
                "target_set_digest": target_set_digest, "evidence_digest": evidence_digest}
            if cutover_digest is not None:
                recovery.update(cutover_epoch=cutover_epoch, cutover_digest=cutover_digest)
            index.update(epoch=new_epoch, latest_reservation_id=None, operations={}, recovery=recovery)
            self._write_index(index)
            return evidence_digest

    def _set_operation_status(self, index: dict, reservation_id: str, status: str) -> None:
        index["operations"][reservation_id]["status"] = status
        self._write_index(index)

    def _validate_grant(self, grant: NativeMutationGrant, canonical_contract: bytes, targets: tuple[str, ...], *, require_fresh=True) -> None:
        if not isinstance(grant, NativeMutationGrant) or grant._seal is not self._seal:
            raise PropagationFenceError("untrusted_grant")
        try:
            parsed = parse_contract(canonical_contract)
        except (TypeError, ValueError) as exc:
            raise PropagationFenceError("malformed_grant") from exc
        if parsed.canonical != canonical_contract:
            raise PropagationFenceError("noncanonical_contract")
        expected_grant = self.grant(canonical_contract=canonical_contract, generation=grant.generation,
                                    effects=grant.effects, target_paths=targets, effect_targets=dict(grant.effect_targets))
        if grant != expected_grant:
            raise PropagationFenceError("grant_binding_mismatch")
        if (grant.owner_id != self.owner.owner_id or grant.resource_id != self.owner.resource_id
                or grant.epoch != self.owner.epoch or grant.contract_digest != parsed.digest
                or grant.generation != parsed.value["generation"]
                or grant.catalog_digest != parsed.value["inputs"]["catalog_digest"]
                or grant.backup_root != str(self.owner.backup_root)
                or grant.targets_digest != _sha256(_canonical(targets))):
            raise PropagationFenceError("grant_binding_mismatch")
        if require_fresh:
            self._require_current_authority(parsed.value)
            self._check_authority(grant.contract_digest, grant.generation)

    def _reservation_id(self, grant: NativeMutationGrant) -> str:
        return _sha256(_canonical(["native-reservation/v1", grant.contract_digest, grant.generation, grant.targets_digest, list(grant.effects), list(grant.effect_targets), grant.backup_root]))

    def _contract_deadline(self, canonical_contract: bytes) -> datetime:
        value = parse_contract(canonical_contract).value
        return self._parse_utc(value["deadline_at"])

    def _require_current_authority(self, value: Mapping[str, object]) -> None:
        now = self.owner.clock()
        if not isinstance(now, datetime) or now.tzinfo != timezone.utc or now.utcoffset() != timezone.utc.utcoffset(now):
            raise PropagationFenceError("malformed_owner_clock")
        issued = self._parse_utc(value["issued_at"])
        deadline = self._parse_utc(value["deadline_at"])
        if issued > now or now >= deadline:
            raise PropagationFenceError("approval_expired")

    @staticmethod
    def _parse_utc(value: object) -> datetime:
        if type(value) is not str or not value.endswith("Z"):
            raise PropagationFenceError("malformed_grant")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise PropagationFenceError("malformed_grant") from exc
        if parsed.tzinfo != timezone.utc or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise PropagationFenceError("malformed_grant")
        return parsed

    def _check_authority(self, contract_digest: str, generation: int) -> None:
        check = self.owner.current_authority
        if not callable(check):
            raise PropagationFenceError("owner_authority_unavailable")
        try:
            accepted = check(contract_digest, generation, self.owner.epoch)
        except Exception:
            accepted = False
        if accepted is not True:
            raise PropagationFenceError("stale_generation")

    def _require_effect_authority(self, state: Mapping[str, object]) -> None:
        self._require_current_authority({"issued_at": "1970-01-01T00:00:00Z", "deadline_at": state["deadline_at"]})
        self._check_authority(state["contract_digest"], state["generation"])

    @staticmethod
    def _validate_generation(index: dict, grant: NativeMutationGrant, reservation_id: str) -> None:
        existing = index["operations"].get(reservation_id)
        if existing is not None:
            if existing["status"] == "completed":
                raise PropagationFenceError("already_completed")
            raise PropagationFenceError("unresolved_reservation")
        if any(row["status"] != "completed" for row in index["operations"].values()):
            raise PropagationFenceError("unresolved_reservation")
        if index["high_water_generation"] > grant.generation:
            raise PropagationFenceError("stale_generation")
        if index["high_water_generation"] == grant.generation:
            raise PropagationFenceError("generation_reused")

    def validate_backup_root(self, path: str | Path) -> None:
        if self._trusted_path(path) != self.owner.backup_root:
            raise PropagationFenceError("backup_root_mismatch")

    def validate_owner_path(self, path: str | Path) -> Path:
        return self._trusted_path(path)

    def _trusted_path(self, path: str | Path) -> Path:
        selected = Path(path).absolute()
        if ".." in selected.parts or not selected.is_relative_to(self.owner.storage_root):
            raise PropagationFenceError("untrusted_target_path")
        return selected

    def _held(self) -> _HeldFiles:
        files = self._files.get()
        if files is None:
            raise PropagationFenceError("native_lock_required")
        return files

    @staticmethod
    def _open_windows_lock(path: Path) -> int:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        # Read/write, share reads/writes (never delete), OPEN_ALWAYS,
        # FILE_FLAG_OPEN_REPARSE_POINT. Validate this exact handle before use.
        handle = create(str(path), 0x80000000 | 0x40000000, 0x1 | 0x2,
                        None, 4, 0x00200000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "native lock open failed")
        try:
            descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDWR | os.O_BINARY)
        except BaseException:
            close = kernel.CloseHandle
            close.argtypes = [wintypes.HANDLE]
            close.restype = wintypes.BOOL
            close(handle)
            raise
        os.set_inheritable(descriptor, False)
        return descriptor

    @contextmanager
    def _snapshot_gate(self, files: _HeldFiles, *, exclusive: bool) -> Iterator[None]:
        parent = files.directory(self.journal_root, create=True, private=True)
        path = self.journal_root / ".snapshot.lock"
        descriptor = (self._open_windows_lock(path) if os.name == "nt" else
                      os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent))
        try:
            files._permissions(descriptor, directory=False, private=True)
            if os.name == "nt":
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, 0)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            yield
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise PropagationFenceError("operation_in_progress") from exc
            raise
        finally:
            os.close(descriptor)

    def snapshot_journal(self, destination: str | Path) -> dict[str, object]:
        """Copy one stable native journal generation under the writer gate."""
        if not hasattr(os, "O_NOFOLLOW"):
            raise PropagationFenceError("snapshot_unsupported")
        selected = Path(destination).absolute()
        if ".." in selected.parts or any(path.is_symlink() for path in (selected, *selected.parents)):
            raise PropagationFenceError("unsafe_custody_path")
        try:
            parent_info = selected.parent.stat()
        except OSError as exc:
            raise PropagationFenceError("unsafe_custody_path") from exc
        if parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o077:
            raise PropagationFenceError("unsafe_custody_path")
        files = _HeldFiles(self.owner.storage_root)
        try:
            files.directory(self.owner.storage_root)
            with self._snapshot_gate(files, exclusive=True):
                parent = files.directory(self.journal_root, private=True)
                names = sorted(name for name in os.listdir(parent) if name.endswith(".json"))
                if len(names) > 10_000:
                    raise PropagationFenceError("snapshot_too_large")
                selected.mkdir(mode=0o700)
                if selected.stat().st_uid != os.geteuid() or selected.stat().st_mode & 0o077:
                    raise PropagationFenceError("unsafe_custody_path")
                manifest: dict[str, str] = {}
                size = 0
                for name in names:
                    if name in {".", ".."} or "/" in name or "\\" in name:
                        raise PropagationFenceError("unsafe_custody_path")
                    data = files.read(self.journal_root / name, private=True)
                    if data is None:
                        raise PropagationFenceError("external_drift")
                    size += len(data)
                    if size > 256 * 1024 * 1024:
                        raise PropagationFenceError("snapshot_too_large")
                    descriptor = os.open(selected / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                    manifest[name] = _sha256(data)
                return {"schema": "anvil-serving.native-journal-snapshot/v1", "files": manifest}
        except (OSError, auth_file.AuthFileError) as exc:
            raise PropagationFenceError("unsafe_custody_path") from exc
        finally:
            files.close()

    @contextmanager
    def _installed_catalog_lock(self, *, borrowed_fd: int | None = None, create: bool = False) -> Iterator[None]:
        """Drain Pi adoption and credential writers before generation activation."""
        path = self.owner.storage_root / ".config" / "anvil-serving" / "pi" / "catalog.lock"
        files = _HeldFiles(self.owner.storage_root)
        fd = None
        try:
            files.directory(self.owner.storage_root)
            parent = files.directory(path.parent, create=create, private=True)
            if borrowed_fd is not None:
                if os.name == "nt" or type(borrowed_fd) is not int or borrowed_fd < 0:
                    raise PropagationFenceError("owner_lock_unavailable")
                fd = borrowed_fd
                actual = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                if not os.path.samestat(actual, os.fstat(fd)):
                    raise PropagationFenceError("owner_lock_unavailable")
            else:
                fd = (self._open_windows_lock(path) if os.name == "nt" else
                      os.open(path.name, os.O_RDWR | (os.O_CREAT if create else 0) | os.O_NOFOLLOW,
                              0o600, dir_fd=parent))
            files._permissions(fd, directory=False, private=True)
            try:
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"0")
                        os.fsync(fd)
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise PropagationFenceError("operation_in_progress") from exc
                raise
            yield
        except (OSError, auth_file.AuthFileError) as exc:
            raise PropagationFenceError("owner_lock_unavailable") from exc
        finally:
            if fd is not None and borrowed_fd is None:
                os.close(fd)
            files.close()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        files = _HeldFiles(self.owner.storage_root)
        token = self._files.set(files)
        fd = None
        try:
            try:
                files.directory(self.owner.storage_root)
                files.directory(self.journal_root.parent, create=True, private=True)
                parent = files.directory(self.journal_root, create=True, private=True)
                lock_path = self.journal_root / (self.owner.resource_id + ".lock")
                fd = (self._open_windows_lock(lock_path) if os.name == "nt" else
                      os.open(lock_path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent))
                files._permissions(fd, directory=False, private=True)
                try:
                    if os.name == "nt":
                        import msvcrt
                        if os.fstat(fd).st_size == 0:
                            os.write(fd, b"0")
                            os.fsync(fd)
                        os.lseek(fd, 0, 0)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in {errno.EACCES, errno.EAGAIN}:
                        raise PropagationFenceError("operation_in_progress") from exc
                    raise
            except (OSError, auth_file.AuthFileError) as exc:
                raise PropagationFenceError("unsafe_custody_path") from exc
            with self._snapshot_gate(files, exclusive=False):
                yield
        finally:
            if fd is not None:
                os.close(fd)
            self._files.reset(token)
            files.close()

    @property
    def _index_path(self) -> Path:
        return self.journal_root / (self.owner.resource_id + ".index.json")

    def _operation_path(self, reservation_id: str) -> Path:
        return self.journal_root / (self.owner.resource_id + "." + _token(reservation_id) + ".json")

    def _read_index(self) -> dict:
        if self._held().read(self._index_path, private=True) is None:
            return {"schema": _SCHEMA + ".index", "owner_id": self.owner.owner_id,
                    "resource_id": self.owner.resource_id, "epoch": self.owner.epoch,
                    "high_water_generation": 0, "latest_reservation_id": None, "operations": {}}
        value = self._read_json(self._index_path)
        keys = {"schema", "owner_id", "resource_id", "epoch", "high_water_generation", "latest_reservation_id", "operations"}
        if (set(value) not in (keys, keys | {"recovery"})
                or value["schema"] != _SCHEMA + ".index" or value["owner_id"] != self.owner.owner_id
                or value["resource_id"] != self.owner.resource_id or value["epoch"] != self.owner.epoch
                or type(value["high_water_generation"]) is not int or value["high_water_generation"] < 0
                or (value["latest_reservation_id"] is not None and type(value["latest_reservation_id"]) is not str)
                or type(value["operations"]) is not dict):
            raise PropagationFenceError("unsafe_journal")
        if "recovery" in value:
            recovery = value["recovery"]
            basic = {"old_epoch", "approval_digest", "target_set_digest", "evidence_digest"}
            if (type(recovery) is not dict or set(recovery) not in (basic, basic | {"cutover_epoch", "cutover_digest"})
                    or recovery["old_epoch"] == value["epoch"]):
                raise PropagationFenceError("unsafe_journal")
            _token(recovery["old_epoch"])
            for key in ("approval_digest", "target_set_digest", "evidence_digest"):
                _digest(recovery[key])
            if "cutover_epoch" in recovery:
                _token(recovery["cutover_epoch"])
                _digest(recovery["cutover_digest"])
        for reservation, row in value["operations"].items():
            _token(reservation)
            if (type(row) is not dict or set(row) != {"generation", "status"}
                    or type(row["generation"]) is not int or row["generation"] < 1
                    or row["status"] not in {"active", "completed", "recovery_required"}):
                raise PropagationFenceError("unsafe_journal")
        return value

    def _read_state(self, path: Path) -> dict:
        value = self._read_json(path)
        required = {"schema", "owner_id", "resource_id", "epoch", "contract_digest", "generation", "catalog_digest", "deadline_at", "effect_targets", "targets_digest", "allowed_effects", "reservation_id", "status", "effects", "updated_at"}
        if (set(value) != required or value["schema"] != _SCHEMA or value["owner_id"] != self.owner.owner_id
                or value["resource_id"] != self.owner.resource_id or value["epoch"] != self.owner.epoch
                or type(value["generation"]) is not int or value["generation"] < 1
                or value["status"] not in {"active", "completed", "recovery_required"}
                or type(value["effects"]) is not dict or type(value["effect_targets"]) is not dict):
            raise PropagationFenceError("unsafe_journal")
        _digest(value["contract_digest"]); _digest(value["catalog_digest"]); _digest(value["targets_digest"])
        return value

    def _read_json(self, path: Path) -> dict:
        try:
            value = json.loads(self._held().read(path, private=True))
        except (OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PropagationFenceError("unsafe_journal") from exc
        if type(value) is not dict:
            raise PropagationFenceError("unsafe_journal")
        return value

    def _write_index(self, value: dict) -> None:
        self._write_json(self._index_path, value)

    def _write_state(self, path: Path, value: dict) -> None:
        value["updated_at"] = self._now()
        self._write_json(path, value)

    def _write_json(self, path: Path, value: dict) -> None:
        self._held().write(path, _canonical(value), private=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["NativeMutationFence", "NativeMutationGrant", "NativeMutationJournal", "PropagationFenceError", "TrustedNativeOwner"]
