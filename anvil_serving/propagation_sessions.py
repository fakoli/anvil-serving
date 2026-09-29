"""Read-only session evidence for installed propagation owner profiles.

No observer starts, stops, reloads, or selects a model. An idle snapshot is not
an admission lock. These projections are internal owner evidence, not receipts
or caller-supplied authority; the profile authenticates and pins each transport.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
import re
import urllib.parse

from .control_plane.propagation import ReceiptIdentity, PropagationContract, _digest, _id, _utc

_MAX_RESPONSE = 64 * 1024
_SESSION_CHECKS = {"new_session": "session-new-loaded", "existing_session": "session-existing-loaded"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def pi_model_digest(model: dict) -> str:
    """Digest loaded routing/limit fields, excluding headers and credentials."""
    fields = ("provider", "id", "api", "baseUrl", "contextWindow", "maxTokens")
    if not isinstance(model, dict) or model.get("provider") != "anvil":
        raise ValueError("incomplete_loaded_model")
    if any(type(model.get(k)) is not str or not 1 <= len(model[k].encode("utf-8")) <= 4096
           for k in fields[:4]):
        raise ValueError("incomplete_loaded_model")
    if any(type(model.get(k)) is not int or not 0 < model[k] <= 16_777_216 for k in fields[4:]):
        raise ValueError("incomplete_loaded_model")
    return _hash(["pi-session-model/v1", *(model[key] for key in fields)])


def pi_catalog_digest(models: list[dict]) -> str:
    """Hash a loaded Anvil catalog, refusing ambiguous or incomplete rows."""
    if not isinstance(models, list) or len(models) > 4096:
        raise ValueError("invalid_loaded_catalog")
    seen, digests = set(), []
    for model in models:
        if not isinstance(model, dict) or type(model.get("provider")) is not str:
            raise ValueError("invalid_loaded_catalog")
        if model["provider"] != "anvil":
            continue
        digest = pi_model_digest(model)
        if model["id"] in seen:
            raise ValueError("duplicate_loaded_model")
        seen.add(model["id"])
        digests.append(digest)
    return _hash(["pi-session-catalog/v1", sorted(digests)])


@dataclass(frozen=True)
class SessionCheck:
    """One pinned installation/executable/profile and one exact native session.

    Construct from the installed owner profile, including its required session
    inventory. A new-session check cannot substitute for an existing-session ID.
    """
    target_id: str
    identity: ReceiptIdentity
    expected_identity_digest: str
    executable_digest: str
    session_id: str
    kind: str
    provider: str
    model_id: str
    model_digest: str
    catalog_digest: str

    def __post_init__(self):
        for value in (self.target_id, self.identity.installation_id,
                      self.identity.profile_id, self.identity.runtime_id,
                      self.session_id, self.provider, self.model_id):
            _id(value)
        _digest(self.expected_identity_digest)
        _digest(self.executable_digest)
        _digest(self.model_digest)
        _digest(self.catalog_digest)
        if self.kind not in {"new_session", "existing_session"}:
            raise ValueError("invalid_session_kind")

    @property
    def digest(self):
        return _hash(asdict(self))


def pending(check: SessionCheck, reason="session-acceptance-pending", *, observed_at=None):
    if type(reason) is not str or reason not in {"session-acceptance-pending", "unsupported-capability",
                      "transport-unavailable", "authorization-denied", "identity-mismatch",
                      "invalid-observation", "stale-observation", "loaded-model-mismatch"}:
        raise ValueError("invalid_session_reason")
    if observed_at is not None:
        _utc(observed_at)
    return {"check_digest": check.digest, "observed_at": observed_at,
            "state": "pending", "pending_reason": reason,
            "identity_matches": False, "loaded_model_digest": None, "loaded_catalog_digest": None,
            "reload_supported": False, "reloads": 0}


def _supports_pi_check(check: SessionCheck) -> bool:
    return check.provider == "anvil" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}", check.session_id) is not None


def pi_web_state(check: SessionCheck, value: dict, *, now: datetime):
    """Validate only the scoped native bridge's allowlisted observation."""
    if not _supports_pi_check(check):
        return pending(check, "unsupported-capability")
    if (type(value) is not dict or set(value) != {
            "v", "observed_at", "native_id", "loaded", "busy", "model", "model_digest", "catalog_digest"}
            or type(value["v"]) is not int or value["v"] != 1
            or type(value["loaded"]) is not bool or type(value["busy"]) is not bool):
        return pending(check, "invalid-observation")
    try:
        stamp = value["observed_at"]
        age = (now - _utc(stamp)).total_seconds()
    except (ValueError, TypeError):
        return pending(check, "invalid-observation")
    if not 0 <= age <= 300:
        return pending(check, "stale-observation", observed_at=stamp)
    if value["native_id"] != check.session_id:
        return pending(check, "identity-mismatch", observed_at=stamp)
    if value["loaded"] is False:
        return pending(check, observed_at=stamp)
    model = value["model"]
    if model is None:
        return pending(check, observed_at=stamp)
    if (type(model) is not dict or set(model) != {"provider", "id", "contextWindow", "maxTokens"}
            or any(type(model[k]) is not int or not 0 < model[k] <= 16_777_216 for k in ("contextWindow", "maxTokens"))):
        return pending(check, "invalid-observation", observed_at=stamp)
    if model["provider"] != check.provider or model["id"] != check.model_id:
        return pending(check, "identity-mismatch", observed_at=stamp)
    result = pending(check, observed_at=stamp)
    result["identity_matches"] = True
    for key in ("model_digest", "catalog_digest"):
        if value[key] is not None:
            try:
                _digest(value[key])
            except ValueError:
                return pending(check, "invalid-observation", observed_at=stamp)
    result.update(loaded_model_digest=value["model_digest"], loaded_catalog_digest=value["catalog_digest"])
    if (value["model_digest"], value["catalog_digest"]) == (check.model_digest, check.catalog_digest):
        result.update(state="accepted", pending_reason=None)
    elif value["model_digest"] is not None and value["model_digest"] != check.model_digest:
        result["pending_reason"] = "loaded-model-mismatch"
    return result


def observe_pi_web(check: SessionCheck, *, port: int, token: str,
                   timeout_seconds: int = 5):
    """GET one existing native Pi Web session on the owner's loopback origin.

    The installed profile supplies the port and already-resolved authorization.
    No ambient proxy, redirect, cookie jar, session creation or model refresh.
    Calling this over a remote transport requires the declared native executor.
    """
    if not _supports_pi_check(check):
        return pending(check, "unsupported-capability")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("invalid_session_origin")
    if (type(token) is not str or not 32 <= len(token) <= 256
            or not re.fullmatch(r"[A-Za-z0-9_-]+", token)):
        raise ValueError("invalid_session_authorization")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 10:
        raise ValueError("invalid_session_timeout")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout_seconds)
    try:
        path = "/api/propagation/session-state?id=" + urllib.parse.quote(check.session_id, safe="")
        connection.request("GET", path, headers={"Accept": "application/json",
                                                "Authorization": "Bearer " + token})
        response = connection.getresponse()
        if response.status in (401, 403):
            return pending(check, "authorization-denied")
        if response.status != 200:
            return pending(check, "transport-unavailable")
        raw = response.read(_MAX_RESPONSE + 1)
        if len(raw) > _MAX_RESPONSE:
            return pending(check, "invalid-observation")
        value = json.loads(raw)
        return pi_web_state(check, value, now=datetime.now(timezone.utc))
    except (OSError, http.client.HTTPException, ValueError):
        # Native responses can contain prompts, paths or account data. Never
        # propagate response bodies or exception text into workflow history.
        return pending(check, "transport-unavailable")
    finally:
        connection.close()


def session_inventory_digest(required: tuple[SessionCheck, ...]) -> str:
    """Digest the profile-internal native-session inventory for owner evidence."""
    return _hash(["pi-session-inventory/v1", sorted(check.digest for check in required)])


def session_states(contract: PropagationContract, required: tuple[SessionCheck, ...],
                   observations: list[dict], *, now: datetime):
    """Fold the exact required sessions into per-target loaded-state projections.

    File convergence and real tool/conversation continuation remain separate
    required owner checks. Approved check IDs select required kinds; missing
    evidence is pending and undeclared kinds are explicitly not-required.
    """
    if len(required) > 128 or now.tzinfo != timezone.utc:
        raise ValueError("invalid_session_inventory")
    by_digest = {check.digest: check for check in required}
    native_keys = [(check.target_id, check.session_id) for check in required]
    if len(by_digest) != len(required) or len(set(native_keys)) != len(required):
        raise ValueError("duplicate_session_inventory")
    targets = {target["target_id"]: target for target in contract.value["targets"]}
    for check in required:
        target = targets.get(check.target_id)
        if (target is None or _SESSION_CHECKS[check.kind] not in target["checks"]
                or asdict(check.identity) != {key: target[key] for key in asdict(check.identity)}):
            raise ValueError("conflicting_session_target")
    for check in required:
        if check.expected_identity_digest != targets[check.target_id]["expected_identity_digest"]:
            raise ValueError("session_inventory_mismatch")
    if not isinstance(observations, list) or len(observations) > len(required):
        raise ValueError("invalid_session_observations")
    rows = {}
    fields = set(pending(required[0])) if required else set()
    for row in observations:
        if (type(row) is not dict or set(row) != fields
                or type(row.get("check_digest")) is not str or row["check_digest"] not in by_digest):
            raise ValueError("unexpected_session_observation")
        key = row["check_digest"]
        if key in rows:
            raise ValueError("duplicate_session_observation")
        check = by_digest[key]
        try:
            age = (now - _utc(row["observed_at"])).total_seconds()
        except (ValueError, TypeError):
            age = -1
        accepted = (row["state"] == "accepted" and 0 <= age <= 300
                    and row["loaded_model_digest"] == check.model_digest
                    and row["loaded_catalog_digest"] == check.catalog_digest
                    and row["identity_matches"] is True and row["pending_reason"] is None
                    and row["reload_supported"] is False and type(row["reloads"]) is int
                    and row["reloads"] == 0)
        if accepted:
            rows[key] = row
        else:
            reason = row["pending_reason"] or "invalid-observation"
            if row["state"] == "accepted" and not 0 <= age <= 300:
                reason = "stale-observation"
            # Rebuild a closed, redacted result rather than returning raw fields.
            rows[key] = pending(check, reason, observed_at=row["observed_at"] if age >= 0 else None)
    result = {}
    for target_id, target in targets.items():
        result[target_id] = {}
        for kind, check_id in _SESSION_CHECKS.items():
            checks = [check for check in required if check.target_id == target_id and check.kind == kind]
            values = [rows.get(check.digest, pending(check)) for check in checks]
            failures = [row for row in values if row["state"] != "accepted"]
            declared = check_id in target["checks"]
            result[target_id][kind] = {
                "state": ("not-required" if not declared else
                          "accepted" if values and not failures else "pending"),
                "pending_reason": (None if not declared or values and not failures else
                                   failures[0]["pending_reason"] if failures else "unsupported-capability"),
                "observed_at": min(row["observed_at"] for row in values)
                if values and all(row["observed_at"] is not None for row in values) else None,
            }
    return result
