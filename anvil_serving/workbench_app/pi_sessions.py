"""Private, bounded durable state for isolated Pi task conversations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Mapping, Protocol

from .pi_rpc import PiCommandId, PiRpcClient, PiRpcError


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


class PiSessionError(RuntimeError):
    pass


class PiSessionAccessError(PiSessionError):
    pass


class PiStartUncertain(PiSessionError):
    """A start key was reserved, so retrying must not replay a Pi prompt."""


@dataclass(frozen=True)
class PiTaskBinding:
    principal_id: str
    project_id: str
    task_id: str
    lease_id: str
    runner_id: str
    provider_id: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if type(value) is not str or not 1 <= len(value.encode("utf-8")) <= 192 or any(ord(c) < 32 for c in value):
                raise ValueError("Pi task binding identifiers must be bounded opaque identities")

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PiSession:
    session_id: str
    binding: PiTaskBinding
    created_at: float
    updated_at: float
    start_key: str
    start_fingerprint: str = ""
    model_id: str = ""
    thinking_level: str = ""
    official_session_id: str | None = None
    official_session_file: str | None = None
    resume_file_name: str | None = None
    container_name: str | None = None
    runtime_command: tuple[str, ...] = ()
    status: str = "reserved"
    parent_session_id: str | None = None
    pending_target: dict[str, Any] | None = None
    first_cursor: int = 1
    next_cursor: int = 1


@dataclass(frozen=True)
class PiSessionEvent:
    cursor: int
    timestamp: float
    kind: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class PiEventPage:
    events: tuple[PiSessionEvent, ...]
    next_cursor: int
    gap: bool


class TaskCoordinator(Protocol):
    def validate_pi_binding(self, binding: PiTaskBinding) -> None: ...


class PiSessionStore:
    """Atomic JSON records beneath one private, absolute state root.

    This is intentionally a small store rather than a shared State database.
    The project adapter validates lease ownership before every mutating action.
    """

    def __init__(
        self,
        state_root: Path,
        *,
        runtime_root: Path | None = None,
        max_events: int = 256,
        max_event_bytes: int = 32_768,
        max_sessions: int = 100,
        max_state_bytes: int = 64 * 1024 * 1024,
        now: Callable[[], float] = time.time,
    ) -> None:
        if not state_root.is_absolute():
            raise ValueError("Pi private state root must be absolute")
        if max_events < 1 or max_event_bytes < 256 or not 1 <= max_sessions <= 100 or max_state_bytes < 1024 * 1024:
            raise ValueError("Pi event limits must be positive and useful")
        self._root = state_root
        self._runtime_root = runtime_root or state_root
        self._sessions = state_root / "sessions"
        self._starts = state_root / "starts.json"
        self._max_events = max_events
        self._max_event_bytes = max_event_bytes
        self._max_sessions = max_sessions
        self._max_state_bytes = max_state_bytes
        self._now = now
        self._lock = threading.RLock()
        self._sessions.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._sessions, 0o700)

    def create(self, binding: PiTaskBinding, start_key: str, *, model_id: str = "", thinking_level: str = "", parent_session_id: str | None = None) -> tuple[PiSession, bool]:
        self._safe(start_key)
        with self._lock:
            starts = self._load_starts()
            claimed = starts.get(start_key)
            if claimed:
                session = self.get(claimed)
                if session.binding.fingerprint != binding.fingerprint or session.start_fingerprint != self.start_fingerprint(binding, model_id, thinking_level, parent_session_id):
                    raise PiSessionAccessError("start key belongs to a different task binding")
                return session, False
            if parent_session_id is not None:
                parent = self.get(parent_session_id)
                self._assert_same_task(parent.binding, binding)
            if len(tuple(self._sessions.glob("*.json"))) >= self._max_sessions:
                raise PiSessionError("Pi retained session limit reached")
            created = self._now()
            session = PiSession(
                session_id=uuid.uuid4().hex,
                binding=binding,
                created_at=created,
                updated_at=created,
                start_key=start_key,
                start_fingerprint=self.start_fingerprint(binding, model_id, thinking_level, parent_session_id),
                model_id=model_id,
                thinking_level=thinking_level,
                container_name=f"anvil-pi-{uuid.uuid4().hex[:20]}",
                parent_session_id=parent_session_id,
            )
            if parent_session_id is not None:
                parent = self.get(parent_session_id)
                if not parent.official_session_file:
                    raise PiSessionError("Pi branch requires a retained official session file")
                source = Path(parent.official_session_file)
                parent_dir = self.session_dir(parent.session_id).resolve()
                if source.is_symlink() or not source.is_file() or source.resolve().parent != parent_dir:
                    raise PiSessionError("Pi branch source is not a retained session file")
                target = self.session_dir(session.session_id) / "branch-source.jsonl"
                shutil.copy2(source, target)
                session = replace(session, resume_file_name=target.name)
            self._write_session(session, [])
            starts[start_key] = session.session_id
            self._write_json(self._starts, starts)
            return session, True

    @staticmethod
    def start_fingerprint(binding, model_id, thinking_level, parent_session_id):
        return hashlib.sha256(json.dumps([binding.fingerprint, model_id, thinking_level, parent_session_id], separators=(",", ":")).encode()).hexdigest()

    def get(self, session_id: str) -> PiSession:
        self._safe(session_id)
        payload = self._read_json(self._session_path(session_id))
        return self._decode_session(payload["session"])

    def resume(self, session_id: str, binding: PiTaskBinding) -> PiSession:
        session = self.get(session_id)
        if session.binding.fingerprint != binding.fingerprint:
            raise PiSessionAccessError("Pi session binding does not match this task owner")
        return session

    def mark_running(self, session_id: str) -> PiSession:
        return self._replace(session_id, status="running")

    def mark_recoverable(self, session_id: str) -> PiSession:
        return self._replace(session_id, status="recoverable")

    def record_official_state(self, session_id: str, data: Mapping[str, Any]) -> PiSession:
        session = self.get(session_id)
        identity, session_file = data.get("sessionId"), data.get("sessionFile")
        if identity != (session.official_session_id or session.session_id):
            raise PiSessionError("Pi returned a different native session identity")
        if not isinstance(session_file, str):
            raise PiSessionError("Pi state did not provide its native session file")
        relative = PurePosixPath(session_file)
        if relative.parent != PurePosixPath("/sessions") or not _SAFE_ID.fullmatch(relative.name) or not relative.name.endswith(".jsonl"):
            raise PiSessionError("Pi official session file is outside private session storage")
        source = self.session_dir(session_id) / relative.name
        if source.is_symlink() or source.parent.is_symlink() or source.resolve().parent != self.session_dir(session_id).resolve():
            raise PiSessionError("Pi official session file cannot traverse a symlink")
        # Official Pi creates this file only after its first assistant message.
        # Keep the live canonical path, never a snapshot that misses later turns.
        if source.exists() and not source.is_file():
            raise PiSessionError("Pi official session path is not a regular file")
        return self._replace(session_id, official_session_id=identity, official_session_file=str(source), resume_file_name=source.name)

    def all(self) -> tuple[PiSession, ...]:
        with self._lock:
            return tuple(self._decode_session(self._read_json(path)["session"]) for path in self._sessions.glob("*.json"))

    def find_start(self, binding: PiTaskBinding, start_key: str) -> PiSession | None:
        self._safe(start_key)
        key = self._load_starts().get(start_key)
        return self.resume(key, binding) if key else None

    def delete(self, session_id: str, binding: PiTaskBinding) -> None:
        with self._lock:
            self.resume(session_id, binding)
            starts = {key: value for key, value in self._load_starts().items() if value != session_id}
            self._write_json(self._starts, starts)
            self._session_path(session_id).unlink()
            for parent in ("runner-sessions", "agents"):
                target = self._runtime_root / parent / session_id
                if target.is_symlink():
                    target.unlink()
                elif target.exists():
                    shutil.rmtree(target)

    def append(self, session_id: str, kind: str, data: Mapping[str, Any]) -> PiSessionEvent:
        if not isinstance(kind, str) or not kind or len(kind) > 120:
            raise ValueError("event kind must be bounded")
        payload = self._bounded_data(data)
        with self._lock:
            stored = self._read_json(self._session_path(session_id))
            session = self._decode_session(stored["session"])
            event = PiSessionEvent(session.next_cursor, self._now(), kind, payload)
            events = [self._decode_event(value) for value in stored.get("events", [])]
            events.append(event)
            events = events[-self._max_events :]
            updated = replace(session, updated_at=self._now(), first_cursor=events[0].cursor, next_cursor=event.cursor + 1)
            self._write_session(updated, events)
            return event

    def events_after(self, session_id: str, cursor: int, *, limit: int = 100) -> PiEventPage:
        if cursor < 0 or limit < 1:
            raise ValueError("cursor and limit must be positive")
        with self._lock:
            stored = self._read_json(self._session_path(session_id))
            session = self._decode_session(stored["session"])
            events = [self._decode_event(value) for value in stored.get("events", [])]
        gap = cursor < session.first_cursor - 1
        selected = tuple(event for event in events if event.cursor > cursor)[:limit]
        return PiEventPage(selected, selected[-1].cursor if selected else cursor, gap)

    def list_for(self, binding: PiTaskBinding) -> tuple[PiSession, ...]:
        with self._lock:
            result: list[PiSession] = []
            for path in self._sessions.glob("*.json"):
                session = self._decode_session(self._read_json(path)["session"])
                if session.binding.fingerprint == binding.fingerprint:
                    result.append(session)
        return tuple(sorted(result, key=lambda value: value.updated_at, reverse=True))

    def session_dir(self, session_id: str) -> Path:
        """Private session storage passed to the runner; never return it to the browser."""
        self._safe(session_id)
        path = self._runtime_root / "runner-sessions" / session_id
        if path.is_symlink() or path.parent.is_symlink():
            raise PiSessionError("Pi session storage cannot be a symlink")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
        return path

    def _replace(self, session_id: str, **changes: Any) -> PiSession:
        with self._lock:
            stored = self._read_json(self._session_path(session_id))
            session = self._decode_session(stored["session"])
            updated = replace(session, **changes, updated_at=self._now())
            self._write_session(updated, [self._decode_event(value) for value in stored.get("events", [])])
            return updated

    def _write_session(self, session: PiSession, events: list[PiSessionEvent]) -> None:
        self._write_json(self._session_path(session.session_id), {"session": asdict(session), "events": [asdict(event) for event in events]})

    def _session_path(self, session_id: str) -> Path:
        self._safe(session_id)
        return self._sessions / f"{session_id}.json"

    @staticmethod
    def _decode_session(value: Mapping[str, Any]) -> PiSession:
        binding = PiTaskBinding(**value["binding"])
        return PiSession(**{**value, "binding": binding, "runtime_command": tuple(value.get("runtime_command", ()))})

    @staticmethod
    def _decode_event(value: Mapping[str, Any]) -> PiSessionEvent:
        return PiSessionEvent(**value)

    def _load_starts(self) -> dict[str, str]:
        if not self._starts.exists():
            return {}
        return dict(self._read_json(self._starts))

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError as exc:
            raise PiSessionError("Pi session does not exist") from exc

    def over_quota(self) -> bool:
        return sum(item.stat().st_size for item in self._root.rglob("*") if item.is_file()) >= self._max_state_bytes

    def _write_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        current = sum(item.stat().st_size for item in self._root.rglob("*") if item.is_file() and item != path)
        if current + len(encoded) > self._max_state_bytes and (not path.exists() or len(encoded) > path.stat().st_size + 2048):
            raise PiSessionError("Pi retained state size limit reached")
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded.decode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _bounded_data(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(data, Mapping):
            raise ValueError("event data must be an object")
        encoded = json.dumps(dict(data), ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > self._max_event_bytes:
            raise ValueError("Pi event exceeds configured storage limit")
        return json.loads(encoded)

    @staticmethod
    def _safe(value: str) -> None:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("identifier must be a safe bounded ID")

    @staticmethod
    def _assert_same_task(left: PiTaskBinding, right: PiTaskBinding) -> None:
        if (left.principal_id, left.project_id, left.task_id, left.lease_id, left.runner_id) != (
            right.principal_id,
            right.project_id,
            right.task_id,
            right.lease_id,
            right.runner_id,
        ):
            raise PiSessionAccessError("cannot branch across Pi task bindings")


class PiSessionService:
    """Coordinates durable start reservations with a root-owned runner factory.

    A repeated start key returns the durable reservation and deliberately does
    not invoke the factory again.  The caller can show recovery rather than
    replaying an uncertain model operation.
    """

    def __init__(self, store: PiSessionStore, coordinator: TaskCoordinator, runner_factory: Callable[[PiSession, Path], None]) -> None:
        self._store = store
        self._coordinator = coordinator
        self._runner_factory = runner_factory

    def start(self, binding: PiTaskBinding, start_key: str, *, parent_session_id: str | None = None) -> PiSession:
        self._coordinator.validate_pi_binding(binding)
        session, created = self._store.create(binding, start_key, parent_session_id=parent_session_id)
        if not created:
            if session.status == "reserved":
                raise PiStartUncertain("Pi start was already reserved; recover it instead of replaying")
            return self._store.resume(session.session_id, binding)
        try:
            self._runner_factory(session, self._store.session_dir(session.session_id))
        except Exception:
            # Reservation remains durable.  A retry must use recovery, never a second launch.
            raise
        return self._store.mark_running(session.session_id)

    def resume(self, session_id: str, binding: PiTaskBinding) -> PiSession:
        self._coordinator.validate_pi_binding(binding)
        return self._store.resume(session_id, binding)


class PiConversationService:
    """JSON-friendly task-chat facade used by the authenticated HTTP routes.

    It owns live process handles independently from browsers. Each route call
    revalidates the task lease through the injected coordinator before it can
    read, start, or command a session.
    """

    def __init__(
        self,
        store: PiSessionStore,
        coordinator: TaskCoordinator,
        runner_factory: Callable[[PiSession, Path], PiRpcClient],
        *,
        allowed_models: Mapping[str, frozenset[str]],
        allowed_thinking: frozenset[str],
        max_active: int = 2,
        max_wall_seconds: int = 4 * 3600,
        max_per_principal: int = 2,
        max_per_task: int = 1,
        now: Callable[[], float] = time.time,
        reconcile: Callable[[PiSession, Path], str] | None = None,
        attach_factory: Callable[[PiSession, Path], PiRpcClient] | None = None,
        stop_runner: Callable[[PiSession], None] | None = None,
    ) -> None:
        self._store = store
        self._coordinator = coordinator
        self._runner_factory = runner_factory
        self._models = {provider: frozenset(models) for provider, models in allowed_models.items()}
        self._thinking = frozenset(allowed_thinking)
        self._max_active = max_active
        self._max_wall = max_wall_seconds
        self._max_per_principal = max_per_principal
        self._max_per_task = max_per_task
        self._now = now
        self._clients: dict[str, PiRpcClient] = {}
        self._started: dict[str, float] = {}
        self._reconcile = reconcile or (lambda _session, _path: "unsafe")
        self._attach_factory = attach_factory
        self._stop_runner = stop_runner or (lambda _session: None)
        self._active: set[str] = set()
        self._changes: dict[str, dict[str, Any]] = {}
        self._state_requested: dict[str, float] = {}
        # Retained identities count even when transport was lost or inspect is
        # unavailable. Only definitive absence frees their capacity.
        for session in self._store.all():
            if session.status in {"reserved", "starting", "running", "recoverable", "quarantined"}:
                try:
                    mode = self._reconcile(session, self._store.session_dir(session.session_id))
                except Exception:
                    mode = "unavailable"
                if mode not in {"absent", "stopped"}:
                    self._active.add(session.session_id)
                self._store.mark_recoverable(session.session_id)

    def capacity(self, principal_id: str, project_id: str, task_id: str, *, exclude: str | None = None) -> None:
        sessions = [self._store.get(key) for key in self._active if key != exclude]
        if (len(sessions) >= self._max_active
                or sum(s.binding.principal_id == principal_id for s in sessions) >= self._max_per_principal
                or sum((s.binding.project_id, s.binding.task_id) == (project_id, task_id) for s in sessions) >= self._max_per_task):
            raise PiSessionError("Pi active runner limit reached; stop a retained runner first")

    def close(self) -> None:
        for session_id in tuple(self._active):
            try:
                self.stop(session_id, self._store.get(session_id).binding, validate=False)
            except Exception:
                # Preserve unresolved runners in durable state for next startup.
                self._store.mark_recoverable(session_id)
                client = self._clients.pop(session_id, None)
                if client:
                    client.close()

    def stop(self, session_id: str, binding: PiTaskBinding, *, validate: bool = True) -> dict[str, Any]:
        session = self._store.resume(session_id, binding)
        if validate:
            self._coordinator.validate_pi_binding(binding)
        self._stop_runner(session)  # Inspector must prove ownership before stop.
        client = self._clients.pop(session_id, None)
        if client:
            client.close()
        self._active.discard(session_id)
        self._started.pop(session_id, None)
        return self._session_json(self._store.mark_recoverable(session_id))

    def delete(self, session_id: str, binding: PiTaskBinding) -> dict[str, Any]:
        session = self._store.resume(session_id, binding)
        if self._reconcile(session, self._store.session_dir(session_id)) not in {"absent", "stopped"} or session_id in self._clients:
            raise PiSessionError("Stop the proven Pi runner before deleting retained history")
        self._store.delete(session_id, binding)
        self._active.discard(session_id)
        return {"deleted": True, "session_id": session_id}


    def list(self, binding: PiTaskBinding) -> list[dict[str, Any]]:
        self._coordinator.validate_pi_binding(binding)
        return [self._session_json(value) for value in self._store.list_for(binding)]

    def read(self, session_id: str, binding: PiTaskBinding) -> dict[str, Any]:
        self._coordinator.validate_pi_binding(binding)
        return self._session_json(self._store.resume(session_id, binding))

    def events(self, session_id: str, binding: PiTaskBinding, cursor: int, *, limit: int = 100) -> dict[str, Any]:
        self._coordinator.validate_pi_binding(binding)
        self._store.resume(session_id, binding)
        self._collect(session_id)
        page = self._store.events_after(session_id, cursor, limit=limit)
        return {
            "events": [asdict(event) for event in page.events],
            "next_cursor": page.next_cursor,
            "gap": page.gap,
        }

    def new(self, binding: PiTaskBinding, start_key: str, *, model_id: str, thinking_level: str, parent_session_id: str | None = None) -> dict[str, Any]:
        self._coordinator.validate_pi_binding(binding)
        if model_id not in self._models.get(binding.provider_id, ()) or thinking_level not in self._thinking:
            raise PiSessionAccessError("Pi target is not allowed")
        existing = self._store.find_start(binding, start_key)
        if existing:
            if existing.start_fingerprint != self._store.start_fingerprint(binding, model_id, thinking_level, parent_session_id):
                raise PiSessionAccessError("start key belongs to a different conversation target or parent")
            if existing.status == "reserved":
                raise PiStartUncertain("Pi start is uncertain; recover the reserved session")
            return self._session_json(existing)
        if parent_session_id is not None and parent_session_id in self._active:
            raise PiSessionError("Stop the retained parent runner before branching")
        self.capacity(binding.principal_id, binding.project_id, binding.task_id)
        session, _ = self._store.create(binding, start_key, model_id=model_id, thinking_level=thinking_level, parent_session_id=parent_session_id)
        self._active.add(session.session_id)
        return self._start_transport(session, recovered=False)

    def _start_transport(self, session: PiSession, *, recovered: bool, attach: bool = False) -> dict[str, Any]:
        factory = self._attach_factory if attach else self._runner_factory
        assert factory is not None
        try:
            client = factory(session, self._store.session_dir(session.session_id))
            client.start()
            self._clients[session.session_id] = client
            self._started[session.session_id] = session.created_at
            self._store._replace(session.session_id, status="starting")
            client.get_state()
            self._state_requested[session.session_id] = self._now()
            self._store.append(session.session_id, "runner_recovering" if recovered else "runner_starting", {"runner_id": session.binding.runner_id})
            self._collect(session.session_id)
        except Exception:
            self._terminal_fault(session.session_id)
            raise
        return self._session_json(self._store.get(session.session_id))

    def resume(self, session_id: str, binding: PiTaskBinding) -> dict[str, Any]:
        self._coordinator.validate_pi_binding(binding)
        session = self._store.resume(session_id, binding)
        if self._now() - session.created_at >= self._max_wall:
            raise PiSessionError("Pi session wall limit expired; create a new task conversation")
        if session_id in self._clients:
            return self._session_json(session)
        self.capacity(binding.principal_id, binding.project_id, binding.task_id, exclude=session_id)
        mode = self._reconcile(session, self._store.session_dir(session_id))
        if mode not in {"running", "absent", "stopped", "pending"} or (mode == "running" and self._attach_factory is None):
            self._store.mark_recoverable(session_id)
            raise PiStartUncertain("Pi recovery could not prove a safe runner state; no command was replayed")
        self._active.add(session_id)
        return self._start_transport(session, recovered=True, attach=mode == "running")

    def command(self, session_id: str, binding: PiTaskBinding, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._coordinator.validate_pi_binding(binding)
        session = self._store.resume(session_id, binding)
        if session.status != "running":
            raise PiStartUncertain("Wait for native Pi identity and target verification before sending commands")
        client = self._clients.get(session_id)
        if client is None:
            raise PiStartUncertain("Pi runner is unavailable; recover without replaying the command")
        command_id: PiCommandId | None
        accepted_metadata: dict[str, Any] = {}
        if name == "prompt":
            message = self._message(payload)
            command_id = client.prompt(message)
            accepted_metadata["message"] = message
        elif name == "steer":
            message = self._message(payload)
            command_id = client.steer(message)
            accepted_metadata["message"] = message
        elif name == "abort":
            command_id = client.abort()
        elif name == "get_state":
            command_id = client.get_state()
        elif name == "set_model":
            provider, model_id = self._text(payload, "provider"), self._text(payload, "model_id")
            if provider != session.binding.provider_id:
                raise PiSessionAccessError("Changing provider requires a new isolated conversation")
            if model_id not in self._models.get(provider, frozenset()):
                raise PiSessionAccessError("model is not allowed for this Pi runner")
            self._persist_target_intent(session, {"model_id": model_id}, name)
            command_id = client.set_model(provider, model_id)
        elif name == "set_thinking_level":
            level = self._text(payload, "level")
            if level not in self._thinking:
                raise PiSessionAccessError("thinking level is not allowed for this Pi runner")
            self._persist_target_intent(session, {"thinking_level": level}, name)
            command_id = client.set_thinking_level(level)
        elif name in {"fork", "clone"}:
            raise PiSessionAccessError("Stop and branch through a new bound conversation")
        elif name == "extension_response":
            request_id = self._text(payload, "request_id")
            response = payload.get("response")
            if not isinstance(response, Mapping):
                raise ValueError("extension response must be an object")
            client.extension_response(request_id, response)
            command_id = None
            accepted_metadata["request_id"] = request_id
        else:
            raise PiSessionAccessError("Pi command is not supported")
        # Retain the bounded private user turn or request identity only after
        # the runner write succeeds. Extension form values remain runner-only.
        accepted = {"name": name, "command_id": command_id} | accepted_metadata
        self._store.append(session_id, "command_accepted", accepted)
        return {"accepted": True, "command_id": command_id}

    def sweep(self) -> tuple[str, ...]:
        """Stop expired retained runners even without a browser or RPC attachment."""
        closed = []
        for session_id in tuple(self._active):
            session = self._store.get(session_id)
            expired = (self._now() - session.created_at >= self._max_wall
                       or self._now() - self._state_requested.get(session_id, self._now()) >= 30)
            try:
                self._coordinator.validate_pi_binding(session.binding)
            except Exception:
                expired = True
            if expired:
                try:
                    self.stop(session_id, session.binding, validate=False)
                    closed.append(session_id)
                except Exception:
                    self._store.mark_recoverable(session_id)
        return tuple(closed)

    def _persist_target_intent(self, session, changes, command):
        old = {"model_id": session.model_id, "thinking_level": session.thinking_level}
        self._store._replace(session.session_id, pending_target={"old": old, "requested": old | changes, "command": command}, status="starting")
        self._state_requested[session.session_id] = self._now()

    def _terminal_fault(self, session_id):
        """Release proven owned capacity even if the protocol or journal is broken."""
        client = self._clients.pop(session_id, None)
        if client:
            try:
                client.close()
            except Exception:
                pass
        self._started.pop(session_id, None)
        self._state_requested.pop(session_id, None)
        stopped = False
        try:
            self._stop_runner(self._store.get(session_id))
            stopped = True
            self._active.discard(session_id)
        except Exception:
            pass
        try:
            self._store._replace(session_id, status="recoverable" if stopped else "quarantined")
            self._store.append(session_id, "runner_fault", {"message": "Pi transport or retained storage failed; no command was replayed", "stopped": stopped, "ok": False})
        except (OSError, ValueError, PiSessionError):
            pass  # Durable old identity still permits later ownership reconciliation.

    def _collect(self, session_id: str) -> None:
        client = self._clients.get(session_id)
        if client is None:
            return
        try:
            if self._store.over_quota():
                raise PiSessionError("Pi retained state limit exceeded")
            for event in client.poll():
                self._store.append(session_id, event.kind, event.data)
            for response in client.responses():
                session = self._store.get(session_id)
                pending = session.pending_target
                if pending and response.command == pending["command"]:
                    # Native acceptance may have happened before a server crash.
                    # Observe state; never resend a target mutation during recovery.
                    pending = pending | {"outcome": "accepted" if response.ok else "rejected"}
                    self._store._replace(session_id, pending_target=pending)
                    client.get_state()
                    self._state_requested[session_id] = self._now()
                if response.command == "get_state":
                    if not response.ok or not isinstance(response.data, Mapping):
                        raise PiSessionError("Pi native state request failed")
                    model = response.data.get("model") or {}
                    actual = {"model_id": model.get("id"), "thinking_level": response.data.get("thinkingLevel")}
                    expected = [pending["old"], pending["requested"]] if pending else [{"model_id": session.model_id, "thinking_level": session.thinking_level}]
                    if pending and pending.get("outcome"):
                        expected = [pending["requested"] if pending["outcome"] == "accepted" else pending["old"]]
                    if model.get("provider") != session.binding.provider_id or actual not in expected:
                        raise PiSessionError("Pi native target differs from its durable allowed target")
                    self._store.record_official_state(session_id, response.data)
                    self._store._replace(session_id, **actual, pending_target=None, status="running")
                    self._state_requested.pop(session_id, None)
                self._store.append(session_id, "command_outcome", {"command_id": response.command_id, "command": response.command, "ok": response.ok, "error": response.error})
                client.take_response(response.command_id)
        except (PiRpcError, PiSessionError, OSError, ValueError, OverflowError):
            self._terminal_fault(session_id)

    @staticmethod
    def _text(payload: Mapping[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _message(payload: Mapping[str, Any]) -> str:
        value = PiConversationService._text(payload, "message")
        if len(value.encode("utf-8")) > 16_000:
            raise ValueError("message exceeds the retained conversation limit")
        return value

    @staticmethod
    def _session_json(session: PiSession) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "status": session.status,
            "parent_session_id": session.parent_session_id,
            "provider_id": session.binding.provider_id,
            "model_id": session.model_id,
            "thinking_level": session.thinking_level,
            "official_session_id": session.official_session_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "first_cursor": session.first_cursor,
            "next_cursor": session.next_cursor,
        }
