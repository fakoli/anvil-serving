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
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterable, Iterator


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "anvil-serving.propagation-native-journal/v1"


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


@dataclass(frozen=True, slots=True)
class TrustedNativeOwner:
    """Private bootstrap data; public callers must not derive it from paths."""

    owner_id: str
    resource_id: str
    storage_root: Path
    epoch: str = "epoch-1"

    def __post_init__(self) -> None:
        _token(self.owner_id)
        _token(self.resource_id)
        _token(self.epoch)
        object.__setattr__(self, "storage_root", Path(self.storage_root).resolve())


@dataclass(frozen=True, slots=True)
class NativeMutationGrant:
    """In-process sealed authority resolved by the authenticated native owner."""

    owner_id: str
    resource_id: str
    epoch: str
    contract_digest: str
    generation: int
    effects: tuple[str, ...]
    targets_digest: str
    _seal: object = field(repr=False, compare=False)


class NativeMutationJournal:
    """Durably records original and observed bytes before each native effect."""

    def __init__(self, fence: "NativeMutationFence", state: dict) -> None:
        self._fence = fence
        self._state = state

    @property
    def reservation_id(self) -> str:
        return self._state["reservation_id"]

    @property
    def started_effects(self) -> tuple[str, ...]:
        return tuple(sorted(self._state["effects"]))

    def is_verified(self, effect_id: str) -> bool:
        return self._effect(effect_id)["state"] == "verified"

    def begin_effect(self, effect_id: str, path: str | Path, before: bytes | None, desired: bytes) -> None:
        effect_id = _token(effect_id)
        selected = self._fence._trusted_path(path)
        if effect_id not in self._state["allowed_effects"]:
            raise PropagationFenceError("effect_not_granted")
        effects = self._state["effects"]
        existing = effects.get(effect_id)
        original_digest = None if before is None else _sha256(before)
        desired_digest = _sha256(desired)
        if existing is not None:
            if (existing.get("path") != str(selected)
                    or existing.get("before_digest") != original_digest
                    or existing.get("desired_digest") != desired_digest):
                raise PropagationFenceError("effect_identity_conflict")
            return
        effects[effect_id] = {
            "path": str(selected),
            "before_digest": original_digest,
            "desired_digest": desired_digest,
            "backup_id": None,
            "state": "prepared",
            "observed_digest": None,
        }
        self._fence._write_state(self._state)

    def bind_backup(self, effect_id: str, backup_id: str) -> None:
        effect = self._effect(effect_id)
        effect["backup_id"] = _token(backup_id)
        self._fence._write_state(self._state)

    def observe_bytes(self, effect_id: str, observed: bytes | None) -> None:
        effect = self._effect(effect_id)
        digest = None if observed is None else _sha256(observed)
        effect["observed_digest"] = digest
        effect["state"] = "verified" if digest == effect["desired_digest"] else "drift"
        self._fence._write_state(self._state)

    def mark_uncertain(self, effect_id: str) -> None:
        effect = self._effect(effect_id)
        effect["state"] = "uncertain"
        self._fence._write_state(self._state)

    def _effect(self, effect_id: str) -> dict:
        effect = self._state["effects"].get(_token(effect_id))
        if not isinstance(effect, dict):
            raise PropagationFenceError("effect_not_started")
        return effect


class NativeMutationFence:
    """One per-owner/resource lock plus a durable no-timeout reservation."""

    def __init__(self, owner: TrustedNativeOwner, journal_root: str | Path) -> None:
        if not isinstance(owner, TrustedNativeOwner):
            raise PropagationFenceError("malformed_owner_context")
        root = Path(journal_root).resolve()
        try:
            root.relative_to(owner.storage_root)
        except ValueError as exc:
            raise PropagationFenceError("untrusted_journal_root") from exc
        self.owner = owner
        self.journal_root = root
        self._seal = object()

    def grant(
        self,
        *,
        canonical_contract: bytes,
        generation: int,
        effects: Iterable[str],
        target_paths: Iterable[str | Path],
    ) -> NativeMutationGrant:
        """Issue a sealed grant after the owner has independently admitted it."""

        if not isinstance(canonical_contract, bytes):
            raise PropagationFenceError("malformed_grant")
        if type(generation) is not int or isinstance(generation, bool) or generation < 1:
            raise PropagationFenceError("malformed_grant")
        allowed = tuple(sorted({_token(effect) for effect in effects}))
        if not allowed:
            raise PropagationFenceError("malformed_grant")
        targets = tuple(sorted(str(self._trusted_path(path)) for path in target_paths))
        if not targets:
            raise PropagationFenceError("malformed_grant")
        return NativeMutationGrant(
            self.owner.owner_id,
            self.owner.resource_id,
            self.owner.epoch,
            _sha256(canonical_contract),
            generation,
            allowed,
            _sha256(_canonical(targets)),
            self._seal,
        )

    @contextmanager
    def transaction(
        self,
        grant: NativeMutationGrant,
        *,
        canonical_contract: bytes,
        target_paths: Iterable[str | Path],
    ) -> Iterator[NativeMutationJournal]:
        """Hold lock, validate generation, and persist outcome before release."""

        if not isinstance(canonical_contract, bytes):
            raise PropagationFenceError("malformed_grant")
        targets = tuple(sorted(str(self._trusted_path(path)) for path in target_paths))
        with self._lock():
            self._validate_grant(grant, canonical_contract, targets)
            previous = self._read_state()
            self._validate_generation(previous, grant)
            state = {
                "schema": _SCHEMA,
                "owner_id": self.owner.owner_id,
                "resource_id": self.owner.resource_id,
                "epoch": grant.epoch,
                "contract_digest": grant.contract_digest,
                "generation": grant.generation,
                "targets_digest": grant.targets_digest,
                "allowed_effects": list(grant.effects),
                "reservation_id": _sha256(_canonical(["native-reservation/v1", grant.contract_digest, grant.generation, grant.targets_digest])),
                "status": "active",
                "effects": {},
                "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
            self._write_state(state)
            journal = NativeMutationJournal(self, state)
            try:
                yield journal
            except BaseException:
                state["status"] = "recovery_required"
                self._write_state(state)
                raise
            if any(effect["state"] != "verified" for effect in state["effects"].values()):
                state["status"] = "recovery_required"
                self._write_state(state)
                raise PropagationFenceError("recovery_required")
            state["status"] = "completed"
            self._write_state(state)

    def journal(self) -> dict | None:
        """Read the bounded public-safe recovery facts without clearing them."""
        with self._lock():
            state = self._read_state()
            return json.loads(_canonical(state).decode("ascii")) if state is not None else None

    def _validate_grant(self, grant: NativeMutationGrant, canonical_contract: bytes, targets: tuple[str, ...]) -> None:
        if not isinstance(grant, NativeMutationGrant) or grant._seal is not self._seal:
            raise PropagationFenceError("untrusted_grant")
        if (grant.owner_id != self.owner.owner_id or grant.resource_id != self.owner.resource_id
                or grant.epoch != self.owner.epoch
                or grant.contract_digest != _sha256(canonical_contract)
                or grant.targets_digest != _sha256(_canonical(targets))):
            raise PropagationFenceError("grant_binding_mismatch")

    @staticmethod
    def _validate_generation(previous: dict | None, grant: NativeMutationGrant) -> None:
        if previous is None:
            return
        if previous["status"] in {"active", "recovery_required"}:
            raise PropagationFenceError("unresolved_reservation")
        if previous["epoch"] != grant.epoch:
            return
        if previous["generation"] > grant.generation:
            raise PropagationFenceError("stale_generation")
        if previous["generation"] == grant.generation:
            raise PropagationFenceError("generation_reused")

    def validate_owner_path(self, path: str | Path) -> Path:
        """Validate non-effect private storage against the native owner root."""

        return self._trusted_path(path)

    def _trusted_path(self, path: str | Path) -> Path:
        selected = Path(path).resolve()
        try:
            selected.relative_to(self.owner.storage_root)
        except ValueError as exc:
            raise PropagationFenceError("untrusted_target_path") from exc
        return selected

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.journal_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.journal_root.is_symlink() or not self.journal_root.is_dir():
            raise PropagationFenceError("unsafe_journal_root")
        lock_path = self.journal_root / (self.owner.resource_id + ".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
                raise PropagationFenceError("unsafe_lock")
            if os.name == "nt":
                import msvcrt
                if info.st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except BlockingIOError as exc:
            raise PropagationFenceError("operation_in_progress") from exc
        finally:
            os.close(fd)

    @property
    def _state_path(self) -> Path:
        return self.journal_root / (self.owner.resource_id + ".json")

    def _read_state(self) -> dict | None:
        path = self._state_path
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise PropagationFenceError("unsafe_journal")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PropagationFenceError("unsafe_journal") from exc
        required = {"schema", "owner_id", "resource_id", "epoch", "contract_digest", "generation", "targets_digest", "allowed_effects", "reservation_id", "status", "effects", "updated_at"}
        info = path.stat()
        if (path.is_symlink() or not stat.S_ISREG(info.st_mode)
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise PropagationFenceError("unsafe_journal")
        if (type(value) is not dict or set(value) != required or value["schema"] != _SCHEMA
                or value["owner_id"] != self.owner.owner_id or value["resource_id"] != self.owner.resource_id
                or value["epoch"] != self.owner.epoch
                or type(value["generation"]) is not int or value["generation"] < 1
                or value["status"] not in {"active", "completed", "recovery_required"}
                or type(value["effects"]) is not dict):
            raise PropagationFenceError("unsafe_journal")
        _digest(value["contract_digest"])
        _digest(value["targets_digest"])
        return value

    def _write_state(self, value: dict) -> None:
        self.journal_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        encoded = _canonical(value)
        fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=self.journal_root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_path)
            if os.name != "nt":
                directory_fd = os.open(self.journal_root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


__all__ = ["NativeMutationFence", "NativeMutationGrant", "NativeMutationJournal", "PropagationFenceError", "TrustedNativeOwner"]
