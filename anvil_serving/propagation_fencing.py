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
from typing import Iterable, Iterator, Mapping

from .control_plane.propagation import parse_contract


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
    catalog_digest: str | None = None

    def __post_init__(self) -> None:
        _token(self.owner_id)
        _token(self.resource_id)
        _token(self.epoch)
        if self.catalog_digest is not None:
            _digest(self.catalog_digest)
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
    effect_targets: tuple[tuple[str, str], ...]
    targets_digest: str
    catalog_digest: str
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
    def started_effects(self) -> tuple[str, ...]:
        return tuple(sorted(self._state["effects"]))

    def is_verified(self, effect_id: str) -> bool:
        return self._effect(effect_id)["state"] == "verified"

    def begin_effect(
        self,
        effect_id: str,
        path: str | Path,
        before: bytes | None,
        desired: bytes | None,
        *,
        semantic_expected: bytes | None = None,
    ) -> None:
        """Record an exact raw before image before a mutation can begin.

        Hermes owns whole YAML files but verifies a normalized key.  For that
        case ``desired`` is deliberately absent and the semantic proof is kept
        separately, so a valid environment reference never masquerades as a
        raw-file digest match.
        """
        effect_id = _token(effect_id)
        selected = self._fence._trusted_path(path)
        if (effect_id not in self._state["allowed_effects"]
                or self._state["effect_targets"].get(effect_id) != str(selected)):
            raise PropagationFenceError("effect_not_granted")
        if desired is not None and not isinstance(desired, bytes):
            raise PropagationFenceError("malformed_effect")
        if semantic_expected is not None and not isinstance(semantic_expected, bytes):
            raise PropagationFenceError("malformed_effect")
        effects = self._state["effects"]
        existing = effects.get(effect_id)
        original_digest = None if before is None else _sha256(before)
        desired_digest = None if desired is None else _sha256(desired)
        semantic_digest = None if semantic_expected is None else _sha256(semantic_expected)
        if existing is not None:
            if (existing.get("path") != str(selected)
                    or existing.get("before_digest") != original_digest
                    or existing.get("desired_digest") != desired_digest
                    or existing.get("semantic_expected_digest") != semantic_digest):
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
        if semantic_digest is not None:
            effect["semantic_expected_digest"] = semantic_digest
            effect["semantic_observed_digest"] = None
        effects[effect_id] = effect
        self._save()

    def bind_backup(self, effect_id: str, backup_id: str) -> None:
        effect = self._effect(effect_id)
        effect["backup_id"] = _token(backup_id)
        self._save()

    def require_before_bytes(self, effect_id: str, observed: bytes | None) -> None:
        effect = self._effect(effect_id)
        digest = None if observed is None else _sha256(observed)
        if digest != effect["before_digest"]:
            effect["observed_digest"] = digest
            effect["state"] = "drift"
            self._save()
            raise PropagationFenceError("external_drift")

    def observe_bytes(self, effect_id: str, observed: bytes | None) -> None:
        effect = self._effect(effect_id)
        digest = None if observed is None else _sha256(observed)
        effect["observed_digest"] = digest
        effect["state"] = "verified" if digest == effect["desired_digest"] else "drift"
        self._save()

    def observe_semantic(self, effect_id: str, after: bytes | None, observed: bytes) -> None:
        """Store raw file readback and separately prove normalized semantics."""
        effect = self._effect(effect_id)
        if "semantic_expected_digest" not in effect:
            raise PropagationFenceError("semantic_proof_not_granted")
        effect["observed_digest"] = None if after is None else _sha256(after)
        effect["semantic_observed_digest"] = _sha256(observed)
        effect["state"] = (
            "verified"
            if effect["semantic_observed_digest"] == effect["semantic_expected_digest"]
            else "drift"
        )
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
        self.journal_root = owner.storage_root / ".anvil-serving" / "propagation-fencing"
        self._seal = object()

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
        if not approved_targets or not declared.intersection({"catalog-apply", "monitoring-apply"}):
            raise PropagationFenceError("effect_not_approved")
        catalog_digest = contract.value["inputs"]["catalog_digest"]
        if self.owner.catalog_digest is not None and self.owner.catalog_digest != catalog_digest:
            raise PropagationFenceError("catalog_expectation_mismatch")
        return NativeMutationGrant(self.owner.owner_id, self.owner.resource_id, self.owner.epoch,
            contract.digest, generation, allowed, mapped, _sha256(_canonical(targets)), catalog_digest, self._seal)

    @contextmanager
    def transaction(self, grant: NativeMutationGrant, *, canonical_contract: bytes,
                    target_paths: Iterable[str | Path]) -> Iterator[NativeMutationJournal]:
        if not isinstance(canonical_contract, bytes):
            raise PropagationFenceError("malformed_grant")
        targets = tuple(sorted(str(self._trusted_path(path)) for path in target_paths))
        with self._lock():
            self._validate_grant(grant, canonical_contract, targets)
            reservation_id = _sha256(_canonical(["native-reservation/v1", grant.contract_digest, grant.generation, grant.targets_digest]))
            index = self._read_index()
            self._validate_generation(index, grant, reservation_id)
            path = self._operation_path(reservation_id)
            state = {
                "schema": _SCHEMA, "owner_id": self.owner.owner_id, "resource_id": self.owner.resource_id,
                "epoch": grant.epoch, "contract_digest": grant.contract_digest, "generation": grant.generation,
                "catalog_digest": grant.catalog_digest, "effect_targets": dict(grant.effect_targets),
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

    def lookup(self, grant: NativeMutationGrant) -> dict | None:
        """Resolve an exact completed identity without allocating a replacement."""
        if not isinstance(grant, NativeMutationGrant) or grant._seal is not self._seal:
            raise PropagationFenceError("untrusted_grant")
        reservation_id = _sha256(_canonical(["native-reservation/v1", grant.contract_digest, grant.generation, grant.targets_digest]))
        return self.journal(reservation_id)

    def _set_operation_status(self, index: dict, reservation_id: str, status: str) -> None:
        index["operations"][reservation_id]["status"] = status
        self._write_index(index)

    def _validate_grant(self, grant: NativeMutationGrant, canonical_contract: bytes, targets: tuple[str, ...]) -> None:
        if not isinstance(grant, NativeMutationGrant) or grant._seal is not self._seal:
            raise PropagationFenceError("untrusted_grant")
        try:
            parsed = parse_contract(canonical_contract)
        except (TypeError, ValueError) as exc:
            raise PropagationFenceError("malformed_grant") from exc
        if parsed.canonical != canonical_contract:
            raise PropagationFenceError("noncanonical_contract")
        if (grant.owner_id != self.owner.owner_id or grant.resource_id != self.owner.resource_id
                or grant.epoch != self.owner.epoch or grant.contract_digest != parsed.digest
                or grant.generation != parsed.value["generation"]
                or grant.catalog_digest != parsed.value["inputs"]["catalog_digest"]
                or grant.targets_digest != _sha256(_canonical(targets))):
            raise PropagationFenceError("grant_binding_mismatch")

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

    def validate_owner_path(self, path: str | Path) -> Path:
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
    def _index_path(self) -> Path:
        return self.journal_root / (self.owner.resource_id + ".index.json")

    def _operation_path(self, reservation_id: str) -> Path:
        return self.journal_root / (self.owner.resource_id + "." + _token(reservation_id) + ".json")

    def _read_index(self) -> dict:
        if not self._index_path.exists():
            return {"schema": _SCHEMA + ".index", "owner_id": self.owner.owner_id,
                    "resource_id": self.owner.resource_id, "epoch": self.owner.epoch,
                    "high_water_generation": 0, "latest_reservation_id": None, "operations": {}}
        value = self._read_json(self._index_path)
        if (set(value) != {"schema", "owner_id", "resource_id", "epoch", "high_water_generation", "latest_reservation_id", "operations"}
                or value["schema"] != _SCHEMA + ".index" or value["owner_id"] != self.owner.owner_id
                or value["resource_id"] != self.owner.resource_id or value["epoch"] != self.owner.epoch
                or type(value["high_water_generation"]) is not int or value["high_water_generation"] < 0
                or (value["latest_reservation_id"] is not None and type(value["latest_reservation_id"]) is not str)
                or type(value["operations"]) is not dict):
            raise PropagationFenceError("unsafe_journal")
        for reservation, row in value["operations"].items():
            _token(reservation)
            if (type(row) is not dict or set(row) != {"generation", "status"}
                    or type(row["generation"]) is not int or row["generation"] < 1
                    or row["status"] not in {"active", "completed", "recovery_required"}):
                raise PropagationFenceError("unsafe_journal")
        return value

    def _read_state(self, path: Path) -> dict:
        value = self._read_json(path)
        required = {"schema", "owner_id", "resource_id", "epoch", "contract_digest", "generation", "catalog_digest", "effect_targets", "targets_digest", "allowed_effects", "reservation_id", "status", "effects", "updated_at"}
        if (set(value) != required or value["schema"] != _SCHEMA or value["owner_id"] != self.owner.owner_id
                or value["resource_id"] != self.owner.resource_id or value["epoch"] != self.owner.epoch
                or type(value["generation"]) is not int or value["generation"] < 1
                or value["status"] not in {"active", "completed", "recovery_required"}
                or type(value["effects"]) is not dict or type(value["effect_targets"]) is not dict):
            raise PropagationFenceError("unsafe_journal")
        _digest(value["contract_digest"]); _digest(value["catalog_digest"]); _digest(value["targets_digest"])
        return value

    def _read_json(self, path: Path) -> dict:
        if path.is_symlink() or not path.is_file():
            raise PropagationFenceError("unsafe_journal")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            info = path.stat()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PropagationFenceError("unsafe_journal") from exc
        if (not stat.S_ISREG(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid())
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077)):
            raise PropagationFenceError("unsafe_journal")
        if type(value) is not dict:
            raise PropagationFenceError("unsafe_journal")
        return value

    def _write_index(self, value: dict) -> None:
        self._write_json(self._index_path, value)

    def _write_state(self, path: Path, value: dict) -> None:
        value["updated_at"] = self._now()
        self._write_json(path, value)

    def _write_json(self, path: Path, value: dict) -> None:
        self.journal_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        encoded = _canonical(value)
        fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=self.journal_root)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                directory_fd = os.open(self.journal_root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["NativeMutationFence", "NativeMutationGrant", "NativeMutationJournal", "PropagationFenceError", "TrustedNativeOwner"]
