"""Bounded native Pi fixture acceptance for approved catalog propagation.

The owner constructs :class:`PiFixtureProfile` from its installed execution
profile.  Workflow input cannot supply a prompt, path, environment, model, or
tool selection.  Existing-session acceptance retains one exact ``PiRpcClient``
across the catalog effect; the client cannot replace a process after start.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from .control_plane.propagation import _id
from .propagation_sessions import NativeSessionCheck, pi_catalog_digest
from .workbench_app.pi_rpc import PiCommandId, PiEvent, PiResponse, PiRpcClient


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_MAX_EVENTS = 8192
_MAX_WEB_RESPONSE = 64 * 1024
_TURN_TIMEOUT_SECONDS = 120


class PiAcceptanceError(RuntimeError):
    """The declared fixture could not produce closed native evidence."""


class PiQuiescenceError(RuntimeError):
    """Cleanup of the exact owned fixture process could not be proved."""


class _PiWebAuthorizationError(PiAcceptanceError):
    """The loopback request was rejected before a fixture action ran."""


class PiClient(Protocol):
    @property
    def started(self) -> bool: ...
    def start(self) -> None: ...
    def get_state(self) -> PiCommandId: ...
    def send(self, command_type: str, **fields: Any) -> PiCommandId: ...
    def prompt(self, message: str) -> PiCommandId: ...
    def poll(self) -> list[PiEvent]: ...
    def take_response(self, command_id: PiCommandId) -> PiResponse | None: ...
    def close(self) -> None: ...


ClientFactory = Callable[[Sequence[str], Path, Mapping[str, str]], PiClient]


def _default_client_factory(
    argv: Sequence[str], cwd: Path, environment: Mapping[str, str],
) -> PiClient:
    return PiRpcClient(
        argv, cwd=cwd, environment=environment, process_factory=_owned_process_factory,
    )


class _OwnedProcess:
    """One POSIX process group owned only by this fixture runner."""

    def __init__(self, process: Any) -> None:
        self._process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self._pgid = process.pid
        self._killed = False

    def _group_exists(self) -> bool:
        try:
            os.killpg(self._pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError as exc:
            raise PiQuiescenceError("cannot inspect the owned fixture process group") from exc

    def poll(self) -> int | None:
        code = self._process.poll()
        return None if code is None or self._group_exists() else code

    def terminate(self) -> None:
        try:
            if os.getpgid(self._process.pid) != self._pgid:
                raise PiQuiescenceError("fixture process group identity changed")
        except ProcessLookupError:
            if not self._group_exists():
                return
        try:
            os.killpg(self._pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def kill(self) -> None:
        self._killed = True
        try:
            os.killpg(self._pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def wait(self, timeout: float | None = None) -> int:
        import subprocess
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            code = self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if self._killed:
                raise PiQuiescenceError("owned fixture process did not exit after kill") from exc
            raise
        while self._group_exists():
            if deadline is not None and time.monotonic() >= deadline:
                if self._killed:
                    raise PiQuiescenceError("owned fixture process group did not become quiescent")
                raise subprocess.TimeoutExpired(self._process.args, timeout)
            time.sleep(0.02)
        return code


def _owned_process_factory(argv: Sequence[str], **kwargs: Any) -> _OwnedProcess:
    if os.name != "posix":
        raise PiAcceptanceError("native Pi fixture process groups require POSIX")
    import subprocess
    process = subprocess.Popen(  # noqa: S603 - argv is installed owner policy.
        list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True, **kwargs,
    )
    return _OwnedProcess(process)


def _utc_seconds(value: datetime) -> str:
    if value.tzinfo != timezone.utc:
        raise PiAcceptanceError("acceptance clock must return UTC")
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    try:
        details = os.lstat(path)
        if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise PiAcceptanceError("declared Pi executable is not a regular file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        digest = hashlib.sha256()
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size) != (
                details.st_dev, details.st_ino, details.st_size,
            ):
                raise PiAcceptanceError("declared Pi executable changed during verification")
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (after.st_dev, after.st_ino, after.st_size) != (
                opened.st_dev, opened.st_ino, opened.st_size,
            ):
                raise PiAcceptanceError("declared Pi executable changed during verification")
        finally:
            os.close(descriptor)
        return digest.hexdigest()
    except OSError as exc:
        raise PiAcceptanceError("declared Pi executable is unavailable") from exc


def _private_directory(path: Path, *, create: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise PiAcceptanceError("fixture directory must be an absolute private path")
    if create:
        try:
            path.mkdir(mode=0o700, parents=False, exist_ok=True)
        except OSError as exc:
            raise PiAcceptanceError("cannot create the fixture directory") from exc
    try:
        details = os.lstat(path)
    except OSError as exc:
        raise PiAcceptanceError("fixture directory is unavailable") from exc
    if (not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode)
            or details.st_uid != os.geteuid() or details.st_mode & 0o077):
        raise PiAcceptanceError("fixture directory is not private to the execution owner")


@dataclass(frozen=True)
class PiFixtureProfile:
    """Installed owner policy for one native Pi runtime.

    ``loaded_catalog_digest`` is the Pi-native catalog digest, distinct from
    the physical catalog-file digest bound into ``NativeSessionCheck``.
    """

    executable: Path
    executable_digest: str
    agent_dir: Path
    fixture_root: Path
    loaded_catalog_digest: str
    environment: Mapping[str, str]

    def __post_init__(self) -> None:
        if (not self.executable.is_absolute() or not self.agent_dir.is_absolute()
                or not self.fixture_root.is_absolute()
                or not _DIGEST.fullmatch(self.executable_digest)
                or not _DIGEST.fullmatch(self.loaded_catalog_digest)):
            raise ValueError("invalid Pi fixture profile")
        if (not isinstance(self.environment, Mapping)
                or self.environment.get("PI_CODING_AGENT_DIR") != str(self.agent_dir)
                or any(type(key) is not str or not key or "\x00" in key
                       or type(value) is not str or "\x00" in value
                       for key, value in self.environment.items())):
            raise ValueError("invalid Pi fixture environment")


@dataclass(frozen=True)
class PiWebFixtureProfile:
    """Installed Pi Web bridge identity and loopback acceptance transport."""

    port: int
    acceptance_token: str
    bridge_artifact_digest: str
    loaded_catalog_digest: str

    def __post_init__(self) -> None:
        if (type(self.port) is not int or not 1024 <= self.port <= 65535
                or type(self.acceptance_token) is not str
                or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", self.acceptance_token)
                or not _DIGEST.fullmatch(self.bridge_artifact_digest)
                or not _DIGEST.fullmatch(self.loaded_catalog_digest)):
            raise ValueError("invalid Pi Web fixture profile")


@dataclass
class PreparedPiFixture:
    """One live pre-effect fixture whose client cannot replace its child."""

    profile: PiFixtureProfile
    native_session_id: str
    process_generation: str
    prepared_at: str
    history_marker: str
    message_count: int
    client: PiClient
    closed: bool = False

    def close(self) -> None:
        if not self.closed:
            self.client.close()
            self.closed = True

    def fixture_record(
        self, *, fixture_id: str, target_id: str, contract_digest: str,
    ) -> dict[str, object]:
        """Return the closed protected pre-effect fixture record."""
        try:
            _id(fixture_id)
            _id(target_id)
        except ValueError as exc:
            raise PiAcceptanceError("invalid Pi fixture binding") from exc
        if not _DIGEST.fullmatch(contract_digest):
            raise PiAcceptanceError("invalid Pi fixture binding")
        return {
            "schema": "native-session-fixture/v1",
            "fixture_id": fixture_id,
            "target_id": target_id,
            "contract_digest": contract_digest,
            "executable_digest": self.profile.executable_digest,
            "prepared_at": self.prepared_at,
            "native_session_id": self.native_session_id,
            "process_generation": self.process_generation,
            "history_marker": self.history_marker,
        }


@dataclass
class PreparedPiWebFixture:
    """One fixture retained by the already-managed Pi Web process."""

    profile: PiWebFixtureProfile
    operation_id: str
    native_session_id: str
    process_generation: str
    prepared_at: str
    history_marker: str
    closed: bool = False

    def fixture_record(
        self, *, fixture_id: str, target_id: str, contract_digest: str,
    ) -> dict[str, object]:
        try:
            _id(fixture_id)
            _id(target_id)
        except ValueError as exc:
            raise PiAcceptanceError("invalid Pi Web fixture binding") from exc
        if not _DIGEST.fullmatch(contract_digest):
            raise PiAcceptanceError("invalid Pi Web fixture binding")
        return {
            "schema": "native-session-fixture/v1",
            "fixture_id": fixture_id,
            "target_id": target_id,
            "contract_digest": contract_digest,
            "executable_digest": self.profile.bridge_artifact_digest,
            "prepared_at": self.prepared_at,
            "native_session_id": self.native_session_id,
            "process_generation": self.process_generation,
            "history_marker": self.history_marker,
        }


@dataclass(frozen=True)
class _Snapshot:
    native_session_id: str
    provider: str
    model: str
    context_tokens: int
    max_output_tokens: int
    message_count: int
    loaded_catalog_digest: str


class PiFixtureAcceptance:
    """Run fixed, read-only Pi fixture turns and return the closed receipt."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = _default_client_factory,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        token: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self._client_factory = client_factory
        self._now = now
        self._monotonic = monotonic
        self._sleep = sleep
        self._token = token

    def prepare_existing(
        self, profile: PiFixtureProfile, native_session_id: str, *, timeout_seconds: int = 120,
    ) -> PreparedPiFixture:
        """Start and exercise the exact process that must survive the effect."""
        deadline = self._deadline(timeout_seconds)
        client, generation = self._start(profile, native_session_id)
        try:
            initial = self._snapshot(client, deadline)
            if initial.native_session_id != native_session_id:
                raise PiAcceptanceError("Pi returned a conflicting native session ID")
            history_marker = "PI-FIXTURE-" + self._token()
            final = self._probe(
                client, profile, native_session_id, phase="before", marker=history_marker,
                deadline=deadline,
            )
            if final.message_count <= initial.message_count:
                raise PiAcceptanceError("Pi fixture history did not advance")
            return PreparedPiFixture(
                profile=profile,
                native_session_id=native_session_id,
                process_generation=generation,
                prepared_at=_utc_seconds(self._now()),
                history_marker=history_marker,
                message_count=final.message_count,
                client=client,
            )
        except BaseException:
            client.close()
            raise

    def complete_existing(
        self, check: NativeSessionCheck, prepared: PreparedPiFixture, *, timeout_seconds: int = 120,
    ) -> dict[str, object]:
        """Continue the retained pre-effect process; never resume a replacement."""
        if (prepared.closed or check.kind != "existing_session"
                or check.previous_session_id != prepared.native_session_id
                or check.prepared_at != prepared.prepared_at
                or check.previous_process_generation != prepared.process_generation):
            raise PiAcceptanceError("existing Pi fixture does not match the approved check")
        self._bind_check(check, prepared.profile)
        deadline = self._deadline(timeout_seconds)
        started_at = _utc_seconds(self._now())
        try:
            snapshot = self._probe(
                prepared.client, prepared.profile, prepared.native_session_id, phase="after",
                deadline=deadline, previous_marker=prepared.history_marker,
            )
            if snapshot.message_count <= prepared.message_count:
                raise PiAcceptanceError("existing Pi fixture history did not advance")
            return self._receipt(
                check, snapshot, started_at=started_at, completed_at=_utc_seconds(self._now()),
                continuity_kind="same_process",
                process_generation=prepared.process_generation,
                history_probe_passed=True,
            )
        finally:
            prepared.close()

    def accept_new(
        self, check: NativeSessionCheck, profile: PiFixtureProfile, native_session_id: str,
        *, timeout_seconds: int = 120,
    ) -> dict[str, object]:
        """Start one distinct post-effect fixture and prove its loaded state."""
        if (check.kind != "new_session" or native_session_id == check.previous_session_id):
            raise PiAcceptanceError("new Pi fixture is not distinct from the prior session")
        self._bind_check(check, profile)
        deadline = self._deadline(timeout_seconds)
        started_at = _utc_seconds(self._now())
        client, generation = self._start(profile, native_session_id)
        try:
            snapshot = self._probe(
                client, profile, native_session_id, phase="new", deadline=deadline,
            )
            if (snapshot.provider != check.provider or snapshot.model != check.model_id
                    or snapshot.context_tokens != check.context_tokens
                    or snapshot.max_output_tokens != check.max_output_tokens
                    or snapshot.loaded_catalog_digest != profile.loaded_catalog_digest):
                raise PiAcceptanceError("new Pi fixture did not load the approved route and catalog")
            return self._receipt(
                check, snapshot, started_at=started_at, completed_at=_utc_seconds(self._now()),
                continuity_kind="loaded_fixture", process_generation=generation,
                history_probe_passed=False,
            )
        finally:
            client.close()

    def _bind_check(self, check: NativeSessionCheck, profile: PiFixtureProfile) -> None:
        if check.executable_digest != profile.executable_digest:
            raise PiAcceptanceError("Pi executable digest conflicts with the approved check")
        if _sha256_file(profile.executable) != profile.executable_digest:
            raise PiAcceptanceError("installed Pi executable does not match its approved digest")
        _private_directory(profile.fixture_root)

    def _deadline(self, timeout_seconds: int) -> float:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= _TURN_TIMEOUT_SECONDS:
            raise PiAcceptanceError("invalid Pi fixture timeout")
        return self._monotonic() + timeout_seconds

    def _start(self, profile: PiFixtureProfile, session_id: str) -> tuple[PiClient, str]:
        if not _ID.fullmatch(session_id):
            raise PiAcceptanceError("invalid Pi fixture session ID")
        if _sha256_file(profile.executable) != profile.executable_digest:
            raise PiAcceptanceError("installed Pi executable does not match its approved digest")
        _private_directory(profile.fixture_root)
        session_dir = profile.fixture_root / "sessions"
        work = profile.fixture_root / "work"
        probes = profile.fixture_root / "probes"
        for directory in (session_dir, work, probes):
            _private_directory(directory, create=True)
        argv = (
            str(profile.executable), "--mode", "rpc", "--offline",
            "--session-dir", str(session_dir), "--session-id", session_id,
            "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-context-files", "--tools", "read",
        )
        client = self._client_factory(argv, work, dict(profile.environment))
        if client.started:
            raise PiAcceptanceError("Pi fixture factory returned a started client")
        client.start()
        generation = "pi-" + self._token()
        if not _ID.fullmatch(generation):
            client.close()
            raise PiAcceptanceError("invalid Pi process generation")
        return client, generation

    def _snapshot(self, client: PiClient, deadline: float) -> _Snapshot:
        state = self._command(client, client.get_state(), deadline)
        catalog = self._command(client, client.send("get_available_models"), deadline)
        if (type(state) is not dict or type(catalog) is not dict
                or set(catalog) != {"models"} or type(catalog["models"]) is not list):
            raise PiAcceptanceError("Pi returned an incomplete loaded-state response")
        model = state.get("model")
        if type(model) is not dict:
            raise PiAcceptanceError("Pi returned no selected model")
        session_id = state.get("sessionId")
        provider, model_id = model.get("provider"), model.get("id")
        context, output = model.get("contextWindow"), model.get("maxTokens")
        message_count = state.get("messageCount")
        if (type(session_id) is not str or not _ID.fullmatch(session_id)
                or type(provider) is not str or not _ID.fullmatch(provider)
                or type(model_id) is not str or not _ID.fullmatch(model_id)
                or any(type(value) is not int or not 0 < value <= 16_777_216
                       for value in (context, output))
                or type(message_count) is not int or message_count < 0
                or state.get("isStreaming") is not False):
            raise PiAcceptanceError("Pi returned invalid selected-model state")
        try:
            catalog_digest = pi_catalog_digest(catalog["models"])
        except ValueError as exc:
            raise PiAcceptanceError("Pi returned an invalid loaded catalog") from exc
        return _Snapshot(
            native_session_id=session_id, provider=provider, model=model_id,
            context_tokens=context, max_output_tokens=output,
            message_count=message_count, loaded_catalog_digest=catalog_digest,
        )

    def _probe(
        self, client: PiClient, profile: PiFixtureProfile, session_id: str, *,
        phase: str, deadline: float, marker: str | None = None,
        previous_marker: str | None = None,
    ) -> _Snapshot:
        marker = marker or "PI-FIXTURE-" + self._token()
        if not re.fullmatch(r"PI-FIXTURE-[a-f0-9]{32}", marker):
            raise PiAcceptanceError("invalid Pi fixture history marker")
        path = profile.fixture_root / "probes" / f"{phase}-{self._token()}.txt"
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.write(descriptor, marker.encode("ascii"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if previous_marker is None:
                expected_text = marker
                prompt = (
                    "Use the read tool exactly once on this fixture file and reply with only "
                    f"its exact contents: {path}"
                )
            else:
                if not re.fullmatch(r"PI-FIXTURE-[a-f0-9]{32}", previous_marker):
                    raise PiAcceptanceError("invalid prior Pi fixture history marker")
                expected_text = previous_marker + "\n" + marker
                prompt = (
                    "Reply with the exact token from your immediately preceding assistant "
                    "response, then a newline. Use the read tool exactly once on this fixture "
                    "file and append only its exact contents after that newline: " + str(path)
                )
            command_id = client.prompt(prompt)
            response: PiResponse | None = None
            events: list[PiEvent] = []
            while self._monotonic() < deadline:
                batch = client.poll()
                events.extend(batch)
                if len(events) > _MAX_EVENTS:
                    raise PiAcceptanceError("Pi fixture emitted too many events")
                response = response or client.take_response(command_id)
                if any(event.data.get("type") == "error" for event in events):
                    raise PiAcceptanceError("Pi fixture turn failed")
                if response is not None and any(
                    event.data.get("type") == "agent_end" for event in events
                ):
                    break
                self._sleep(0.02)
            if response is None or not response.ok:
                raise PiAcceptanceError("Pi fixture prompt was not accepted")
            if not self._valid_probe_events(
                events, path=path, marker=marker, expected_text=expected_text,
            ):
                raise PiAcceptanceError("Pi fixture tool turn did not complete exactly")
            snapshot = self._snapshot(client, deadline)
            if snapshot.native_session_id != session_id:
                raise PiAcceptanceError("Pi fixture changed native session identity")
            return snapshot
        except OSError as exc:
            raise PiAcceptanceError("cannot create the private Pi fixture probe") from exc
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _valid_probe_events(
        events: list[PiEvent], *, path: Path, marker: str, expected_text: str,
    ) -> bool:
        starts = [event.data for event in events
                  if event.data.get("type") == "tool_execution_start"]
        ends = [event.data for event in events
                if event.data.get("type") == "tool_execution_end"]
        agent_ends = [event for event in events if event.data.get("type") == "agent_end"]
        text_ends = [
            event.data.get("assistantMessageEvent") for event in events
            if event.data.get("type") == "message_update"
            and type(event.data.get("assistantMessageEvent")) is dict
            and event.data["assistantMessageEvent"].get("type") == "text_end"
        ]
        if (len(starts) != 1 or len(ends) != 1 or len(agent_ends) != 1
                or starts[0].get("toolName") != "read"
                or ends[0].get("toolName") != "read"
                or starts[0].get("toolCallId") != ends[0].get("toolCallId")
                or starts[0].get("args") != {"path": str(path)}
                or ends[0].get("isError") is not False
                or not text_ends
                or text_ends[-1].get("content", "").strip() != expected_text):
            return False
        result = ends[0].get("result")
        if type(result) is not dict or type(result.get("content")) is not list:
            return False
        texts = [item.get("text") for item in result["content"]
                 if type(item) is dict and item.get("type") == "text"]
        return texts == [marker]

    def _command(self, client: PiClient, command_id: PiCommandId, deadline: float) -> Any:
        while self._monotonic() < deadline:
            client.poll()
            response = client.take_response(command_id)
            if response is not None:
                if not response.ok:
                    raise PiAcceptanceError("Pi fixture command failed")
                return response.data
            self._sleep(0.02)
        raise PiAcceptanceError("Pi fixture command timed out")

    @staticmethod
    def _receipt(
        check: NativeSessionCheck,
        snapshot: _Snapshot,
        *,
        started_at: str,
        completed_at: str,
        continuity_kind: str,
        process_generation: str,
        history_probe_passed: bool,
    ) -> dict[str, object]:
        return {
            "schema": "native-session-acceptance/v1",
            "check_digest": check.digest,
            "started_at": started_at,
            "completed_at": completed_at,
            "native_session_id": snapshot.native_session_id,
            "continuity_kind": continuity_kind,
            "configured_route": True,
            "provider": snapshot.provider,
            "model": snapshot.model,
            "context_tokens": snapshot.context_tokens,
            "max_output_tokens": snapshot.max_output_tokens,
            "turn_completed": True,
            "tool_probe_passed": True,
            "history_probe_passed": history_probe_passed,
            "fallback_used": False,
            "physical_catalog_digest": check.physical_catalog_digest,
            "process_generation": process_generation,
        }


class PiWebFixtureAcceptance:
    """Call only the fixed loopback Pi Web fixture actions."""

    def prepare_existing(
        self,
        profile: PiWebFixtureProfile,
        operation_id: str,
        native_session_id: str,
        *,
        timeout_seconds: int = 120,
    ) -> PreparedPiWebFixture:
        self._identifiers(operation_id, native_session_id)
        try:
            value = self._post(profile, {
                "v": 1,
                "action": "prepare_existing",
                "operation_id": operation_id,
                "native_id": native_session_id,
            }, timeout_seconds=timeout_seconds)
            fields = {"schema", "prepared_at", "native_session_id", "process_generation", "history_marker"}
            if (set(value) != fields or value.get("schema") != "pi-web-propagation-fixture/v1"
                    or value.get("native_session_id") != native_session_id
                    or type(value.get("process_generation")) is not str
                    or type(value.get("history_marker")) is not str
                    or not re.fullmatch(r"PI-FIXTURE-[a-f0-9]{32}", value["history_marker"])):
                raise PiAcceptanceError("Pi Web returned invalid pre-effect fixture evidence")
            try:
                _id(value["process_generation"])
                stamp = datetime.fromisoformat(str(value["prepared_at"]).replace("Z", "+00:00"))
                if stamp.tzinfo != timezone.utc:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise PiAcceptanceError("Pi Web returned invalid pre-effect fixture evidence") from exc
            return PreparedPiWebFixture(
                profile=profile,
                operation_id=operation_id,
                native_session_id=native_session_id,
                process_generation=value["process_generation"],
                prepared_at=value["prepared_at"],
                history_marker=value["history_marker"],
            )
        except _PiWebAuthorizationError:
            raise
        except PiAcceptanceError:
            self._cancel_identity(profile, operation_id, native_session_id)
            raise

    def complete_existing(
        self,
        check: NativeSessionCheck,
        prepared: PreparedPiWebFixture,
        *,
        timeout_seconds: int = 120,
    ) -> dict[str, object]:
        if (prepared.closed or check.kind != "existing_session"
                or check.previous_session_id != prepared.native_session_id
                or check.prepared_at != prepared.prepared_at
                or check.previous_process_generation != prepared.process_generation):
            raise PiAcceptanceError("existing Pi Web fixture does not match the approved check")
        self._bind_check(check, prepared.profile)
        try:
            value = self._post(prepared.profile, {
                "v": 1,
                "action": "complete_existing",
                "operation_id": prepared.operation_id,
                "native_id": prepared.native_session_id,
            }, timeout_seconds=timeout_seconds)
            receipt = self._receipt(
                check, prepared.profile, value, continuity_kind="same_process",
                expected_native_id=prepared.native_session_id, history_probe_passed=True,
                expected_generation=prepared.process_generation,
            )
        except _PiWebAuthorizationError:
            self._cancel_identity(
                prepared.profile, prepared.operation_id, prepared.native_session_id,
            )
            raise
        except PiAcceptanceError:
            self._cancel_identity(
                prepared.profile, prepared.operation_id, prepared.native_session_id,
            )
            prepared.closed = True
            raise
        prepared.closed = True
        return receipt

    def accept_new(
        self,
        check: NativeSessionCheck,
        profile: PiWebFixtureProfile,
        operation_id: str,
        native_session_id: str,
        *,
        timeout_seconds: int = 120,
    ) -> dict[str, object]:
        self._identifiers(operation_id, native_session_id)
        if check.kind != "new_session" or native_session_id == check.previous_session_id:
            raise PiAcceptanceError("new Pi Web fixture is not distinct from the prior session")
        self._bind_check(check, profile)
        try:
            value = self._post(profile, {
                "v": 1,
                "action": "accept_new",
                "operation_id": operation_id,
                "native_id": native_session_id,
                "previous_native_id": check.previous_session_id,
            }, timeout_seconds=timeout_seconds)
            return self._receipt(
                check, profile, value, continuity_kind="loaded_fixture",
                expected_native_id=native_session_id, history_probe_passed=False,
                expected_generation=None,
            )
        except _PiWebAuthorizationError:
            raise
        except PiAcceptanceError:
            self._cancel_identity(profile, operation_id, native_session_id)
            raise

    def close(
        self, prepared: PreparedPiWebFixture, *, timeout_seconds: int = 10,
    ) -> None:
        if prepared.closed:
            return
        self._cancel_identity(
            prepared.profile, prepared.operation_id, prepared.native_session_id,
            timeout_seconds=timeout_seconds,
        )
        prepared.closed = True

    def _cancel_identity(
        self,
        profile: PiWebFixtureProfile,
        operation_id: str,
        native_session_id: str,
        *,
        timeout_seconds: int = 10,
    ) -> None:
        try:
            value = self._post(profile, {
                "v": 1,
                "action": "cancel_existing",
                "operation_id": operation_id,
                "native_id": native_session_id,
            }, timeout_seconds=timeout_seconds)
            if value != {"schema": "pi-web-propagation-cancel/v1", "closed": True}:
                raise PiAcceptanceError("Pi Web did not close its owned fixture")
        except PiAcceptanceError as exc:
            raise PiQuiescenceError("Pi Web fixture quiescence could not be proved") from exc

    @staticmethod
    def _identifiers(operation_id: str, native_session_id: str) -> None:
        try:
            _id(operation_id)
        except ValueError as exc:
            raise PiAcceptanceError("invalid Pi Web operation ID") from exc
        if not re.fullmatch(r"propagation-[A-Za-z0-9][A-Za-z0-9._-]{0,179}", native_session_id):
            raise PiAcceptanceError("invalid Pi Web native fixture ID")

    @staticmethod
    def _bind_check(check: NativeSessionCheck, profile: PiWebFixtureProfile) -> None:
        if check.executable_digest != profile.bridge_artifact_digest:
            raise PiAcceptanceError("Pi Web bridge artifact conflicts with the approved check")

    @staticmethod
    def _receipt(
        check: NativeSessionCheck,
        profile: PiWebFixtureProfile,
        value: dict[str, Any],
        *,
        continuity_kind: str,
        expected_native_id: str,
        history_probe_passed: bool,
        expected_generation: str | None,
    ) -> dict[str, object]:
        fields = {
            "schema", "started_at", "completed_at", "native_session_id",
            "process_generation", "configured_route", "provider", "model",
            "context_tokens", "max_output_tokens", "loaded_catalog_digest",
            "turn_completed", "tool_probe_passed", "history_probe_passed", "fallback_used",
        }
        if (set(value) != fields or value.get("schema") != "pi-web-propagation-turn/v1"
                or value.get("native_session_id") != expected_native_id
                or value.get("configured_route") is not True
                or value.get("provider") != check.provider or value.get("model") != check.model_id
                or value.get("loaded_catalog_digest") != profile.loaded_catalog_digest
                or value.get("turn_completed") is not True
                or value.get("tool_probe_passed") is not True
                or value.get("history_probe_passed") is not history_probe_passed
                or value.get("fallback_used") is not False
                or type(value.get("process_generation")) is not str
                or expected_generation is not None and value["process_generation"] != expected_generation):
            raise PiAcceptanceError("Pi Web returned invalid native turn evidence")
        for key in ("context_tokens", "max_output_tokens"):
            if type(value[key]) is not int or not 0 < value[key] <= 16_777_216:
                raise PiAcceptanceError("Pi Web returned invalid native limits")
        if check.kind == "new_session" and (
            value["context_tokens"], value["max_output_tokens"],
        ) != (check.context_tokens, check.max_output_tokens):
            raise PiAcceptanceError("new Pi Web fixture did not load the approved limits")
        try:
            _id(value["process_generation"])
            for key in ("started_at", "completed_at"):
                stamp = datetime.fromisoformat(str(value[key]).replace("Z", "+00:00"))
                if stamp.tzinfo != timezone.utc:
                    raise ValueError
        except (TypeError, ValueError) as exc:
            raise PiAcceptanceError("Pi Web returned invalid native turn timestamps") from exc
        return {
            "schema": "native-session-acceptance/v1",
            "check_digest": check.digest,
            "started_at": value["started_at"],
            "completed_at": value["completed_at"],
            "native_session_id": value["native_session_id"],
            "continuity_kind": continuity_kind,
            "configured_route": True,
            "provider": value["provider"],
            "model": value["model"],
            "context_tokens": value["context_tokens"],
            "max_output_tokens": value["max_output_tokens"],
            "turn_completed": True,
            "tool_probe_passed": True,
            "history_probe_passed": history_probe_passed,
            "fallback_used": False,
            "physical_catalog_digest": check.physical_catalog_digest,
            "process_generation": value["process_generation"],
        }

    @staticmethod
    def _post(
        profile: PiWebFixtureProfile,
        body: dict[str, object],
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise PiAcceptanceError("invalid Pi Web fixture timeout")
        connection = http.client.HTTPConnection("127.0.0.1", profile.port, timeout=timeout_seconds)
        try:
            connection.request(
                "POST", "/api/propagation/session-acceptance",
                body=json.dumps(body, separators=(",", ":"), ensure_ascii=False),
                headers={
                    "Accept": "application/json",
                    "Authorization": "Bearer " + profile.acceptance_token,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read(_MAX_WEB_RESPONSE + 1)
            if response.status in (401, 403, 404):
                raise _PiWebAuthorizationError("Pi Web fixture authorization was rejected")
            if response.status != 200 or len(raw) > _MAX_WEB_RESPONSE:
                raise PiAcceptanceError("Pi Web fixture operation is unavailable")
            value = json.loads(raw)
            if type(value) is not dict:
                raise PiAcceptanceError("Pi Web returned invalid fixture evidence")
            return value
        except (OSError, http.client.HTTPException, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, PiAcceptanceError):
                raise
            raise PiAcceptanceError("Pi Web fixture operation is unavailable") from exc
        finally:
            connection.close()


def _write_private_json(path: Path, value: dict[str, object]) -> str:
    """Create one canonical protected record, allowing byte-identical replay."""
    if not path.is_absolute() or path == Path("/") or ".." in path.parts:
        raise PiAcceptanceError("acceptance record path must be absolute")
    _private_directory(path.parent)
    payload = (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ) + "\n").encode("utf-8")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        try:
            details = os.lstat(path)
            if (not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode)
                    or details.st_uid != os.geteuid() or details.st_mode & 0o077
                    or path.read_bytes() != payload):
                raise PiAcceptanceError("acceptance record conflicts with existing evidence")
        except OSError as exc:
            raise PiAcceptanceError("cannot verify existing acceptance evidence") from exc
    except OSError as exc:
        raise PiAcceptanceError("cannot create protected acceptance evidence") from exc
    else:
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise PiAcceptanceError("cannot sync protected acceptance evidence") from exc
    return hashlib.sha256(payload).hexdigest()


def write_fixture_record(
    path: Path,
    prepared: PreparedPiFixture | PreparedPiWebFixture,
    *,
    fixture_id: str,
    target_id: str,
    contract_digest: str,
) -> str:
    """Persist the pre-effect fixture binding without prompt or account data."""
    return _write_private_json(
        path, prepared.fixture_record(
            fixture_id=fixture_id, target_id=target_id, contract_digest=contract_digest,
        ),
    )


def write_acceptance_receipt(path: Path, receipt: dict[str, object]) -> str:
    """Persist only the exact closed native acceptance receipt fields."""
    fields = {
        "schema", "check_digest", "started_at", "completed_at", "native_session_id",
        "continuity_kind", "configured_route", "provider", "model", "context_tokens",
        "max_output_tokens", "turn_completed", "tool_probe_passed", "history_probe_passed",
        "fallback_used", "physical_catalog_digest", "process_generation",
    }
    if (type(receipt) is not dict or set(receipt) != fields
            or receipt.get("schema") != "native-session-acceptance/v1"):
        raise PiAcceptanceError("invalid closed native acceptance receipt")
    return _write_private_json(path, receipt)
