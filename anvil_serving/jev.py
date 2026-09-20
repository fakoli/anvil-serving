"""Optional typed advice through Anvil's versioned, local CLI bridge.

Serving has no TypeSafe transport or model-call implementation. Policy and
source-export permission are checked before starting the optional executable.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import threading
import time

from .operator_output import redact
from .paths import config_path

CAPABILITIES = ("skill_suggestion", "context_ranking", "incident_triage", "voice_intent")
MODEL = "jev-1.13.0"
MAX_INPUT = 32 * 1024
MAX_OUTPUT = 256 * 1024
DEFAULTS = {"enabled": False, "capabilities": [], "allow_api": False,
            "allow_export": False, "anvil_binary": "", "model": MODEL,
            "api_key_env": "TYPESAFE_API_KEY", "timeout_seconds": 5.0}
INCIDENT_CHECKS = {
    "authentication": "Inspect the selected client's credential reference and authentication status.",
    "authorization_or_license": "Inspect the resource grant and the provider's license or access status.",
    "missing_dependency": "Inspect the declared dependency versions and the earliest startup error.",
    "incompatible_configuration": "Compare the declared configuration with the owning service's supported contract.",
    "resource_exhaustion": "Inspect the selected owner's memory, queue and admission measurements.",
    "connectivity": "Inspect the selected endpoint's reachability and transport status.",
    "application_behavior": "Compare the observed output with a reproducible, independent check.",
    "unknown": "Collect a bounded observation from the existing authorized diagnostic tools.",
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def decode_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def validate_policy(value):
    if type(value) is not dict or set(value) - set(DEFAULTS):
        raise ValueError("Invalid Jev policy fields")
    policy = DEFAULTS | value
    for name in ("enabled", "allow_api", "allow_export"):
        if type(policy[name]) is not bool:
            raise ValueError("Jev permissions must be boolean")
    caps = policy["capabilities"]
    if (type(caps) is not list or len(caps) > len(CAPABILITIES)
            or any(type(item) is not str or item not in CAPABILITIES for item in caps)
            or len(set(caps)) != len(caps)):
        raise ValueError("Invalid Jev capability selection")
    if policy["model"] != MODEL or policy["api_key_env"] != "TYPESAFE_API_KEY":
        raise ValueError("Use the pinned Jev model and credential reference")
    seconds = policy["timeout_seconds"]
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0.1 <= seconds <= 5:
        raise ValueError("Jev deadline must be between 0.1 and 5 seconds")
    binary = policy["anvil_binary"]
    if type(binary) is not str or (binary and (not Path(binary).is_absolute() or any(ord(c) < 32 for c in binary))):
        raise ValueError("Declare an absolute trusted Anvil executable")
    return policy


def policy_path():
    return Path(config_path("jev.json"))


class PolicySnapshot(dict):
    """Private opened-file generation; never persisted or included in payloads."""
    def __init__(self, value, generation=None, raw=None):
        super().__init__(value)
        self.generation, self.raw = generation, raw

    def copy(self):
        return PolicySnapshot(self, self.generation, self.raw)

    def __eq__(self, other):
        return isinstance(other, dict) and dict.__eq__(self, other) and self.generation == getattr(other, "generation", None)

    def __ne__(self, other):
        return not self == other


def _file_generation(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_snapshot(path, limit):
    """Bound reads on the opened regular file; refuse symlinks and FIFOs."""
    if path.is_symlink():
        raise ValueError("Symlink sources are unsupported")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Source must be a regular file")
        raw = source.read(limit + 1)
        if _file_generation(info) != _file_generation(os.fstat(source.fileno())):
            raise ValueError("Source changed while reading")
    if len(raw) > limit:
        raise ValueError("Source exceeds its bound")
    return raw, _file_generation(info)


def read_regular(path, limit):
    return _read_snapshot(path, limit)[0]


def load_policy():
    path = policy_path()
    try:
        raw, generation = _read_snapshot(path, 8192)
    except FileNotFoundError:
        return PolicySnapshot(validate_policy({}))
    return PolicySnapshot(validate_policy(decode_json(raw)), generation, raw)


def status():
    try:
        policy = load_policy()
    except (OSError, ValueError, RecursionError):
        return {"enabled": False, "capabilities": [], "status": "unavailable", "reason": "invalid_configuration", "model": MODEL}
    return {key: policy[key] for key in ("enabled", "capabilities", "allow_api", "allow_export", "model")} | {
        "configured": bool(policy["anvil_binary"]), "provider": "typesafe", "advisory": True,
        "disclosure": "Selected text is processed by TypeSafe/Jev in the cloud. Advice is experimental and has no execution or approval authority.",
    }


def report(capability, state, reason, *, started=False):
    return {"schema": "anvil.jev.annotation.v1", "provider": "typesafe", "model": MODEL,
            "capability": capability, "status": state, "reason": reason, "requested": True,
            "used": False, "request_started": started, "elapsed_ms": 0,
            "rubric_digest": None, "input_digest": None, "answers": {}, "usage": {}, "advisory": True}


def gate(policy, capability, *, allow_export=False, disabled=False):
    if capability not in CAPABILITIES:
        return report(capability, "blocked", "unsupported_capability")
    if disabled or not policy["enabled"] or capability not in policy["capabilities"]:
        return report(capability, "disabled", "capability_disabled")
    if not policy["allow_api"] or not policy["allow_export"] or allow_export is not True:
        return report(capability, "blocked", "export_permission_required")
    if not policy["anvil_binary"]:
        return report(capability, "unavailable", "anvil_unavailable")
    return None


def selected_input(value):
    """Redact known secret patterns in deliberately selected text only."""
    if type(value) is not dict or len(encoded(value)) > MAX_INPUT:
        raise ValueError("Invalid selected Jev input")
    # Configuration/key documents are never an input source. Callers supply
    # snippets, not paths; the CLI also rejects secret-file selections.
    raw = encoded(value).decode()
    if "-----BEGIN " in raw or re.search(r'(?i)"(?:credentials?|environment|private_key|api_key)"\s*:', raw):
        raise ValueError("Secret sources cannot be exported")
    return redact(value)


def validate_input(capability, value):
    if type(value) is not dict:
        raise ValueError("Invalid selected input")
    field = "observation" if capability == "incident_triage" else "text" if capability == "voice_intent" else "intent"
    expected = {field} | ({"candidates"} if capability in {"skill_suggestion", "context_ranking"} else set())
    if (set(value) != expected or type(value.get(field)) is not str or not value[field].strip()
            or len(value[field]) > 4096 or len(value[field].encode()) > 8192):
        raise ValueError("Invalid selected text")
    if "candidates" in expected:
        rows = value["candidates"]
        detail = "description" if capability == "skill_suggestion" else "text"
        if type(rows) is not list or not 1 <= len(rows) <= 24:
            raise ValueError("Select between one and 24 candidates")
        seen = set()
        for row in rows:
            if (type(row) is not dict or set(row) != {"id", detail} or type(row["id"]) is not str
                    or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,47}", row["id"])
                    or row["id"].lower() == "none" or row["id"] in seen
                    or redact(row["id"]) != row["id"]
                    or type(row[detail]) is not str or not row[detail].strip() or len(row[detail].encode()) > 4096):
                raise ValueError("Invalid selected candidate")
            seen.add(row["id"])
    return value


def _bridge(policy, payload, environment):
    from .control_plane.mcp.runtime import _process_group_options, _terminate_process_tree

    # Forward only the explicitly selected credential; never discover or load
    # account stores. Its availability/validity is owned by the Anvil bridge.
    names = ("PATH", "HOME", "LANG", "SYSTEMROOT", "TYPESAFE_API_KEY")
    env = {key: environment[key] for key in names if key in environment}
    env.update(NO_COLOR="1", TERM="dumb")
    output = bytearray()
    done = threading.Event()
    failed = threading.Event()
    deadline = time.monotonic() + policy["timeout_seconds"] + 1
    with tempfile.TemporaryFile() as source:
        source.write(encoded(payload))
        source.seek(0)
        process = subprocess.Popen([policy["anvil_binary"], "jev", "bridge", "--json"],
            stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            cwd=str(Path(policy["anvil_binary"]).parent), **_process_group_options())

        def drain():
            try:
                while chunk := process.stdout.read1(8192):
                    if len(output) + len(chunk) > MAX_OUTPUT:
                        failed.set()
                        return
                    output.extend(chunk)
            except (OSError, ValueError):
                failed.set()
            finally:
                done.set()

        reader = threading.Thread(target=drain, daemon=True, name="jev-bridge-output")
        reader.start()
        try:
            if not done.wait(max(0, deadline - time.monotonic())) or failed.is_set():
                raise ValueError("Bridge output unavailable")
            if process.wait(timeout=max(0.01, deadline - time.monotonic())):
                raise ValueError("Bridge failed")
        finally:
            if process.poll() is None or reader.is_alive():
                _terminate_process_tree(process)
            reader.join(timeout=1)
            if not reader.is_alive():
                process.stdout.close()
    return decode_json(output)


def advise(capability, value, *, allow_export=False, disabled=False, environment=None, policy_reader=load_policy, bridge=_bridge):
    try:
        policy = policy_reader()
        denied = gate(policy, capability, allow_export=allow_export, disabled=disabled)
        if denied:
            return denied
        value = selected_input(validate_input(capability, value))
        payload = {"jev": {key: policy[key] for key in ("enabled", "capabilities", "model", "timeout_seconds", "api_key_env")},
                   "allow_api": True, "allow_export": True, "capability": capability, "input": value}
        if len(encoded(payload)) > MAX_INPUT:
            return report(capability, "blocked", "input_too_large")
        # Revalidate the owner's policy/consent after input preparation, at the
        # last local dispatch boundary. Revocation cannot recall sent bytes.
        if policy_reader() != policy:
            return report(capability, "blocked", "policy_changed")
    except (OSError, ValueError, TypeError, RecursionError):
        return report(capability, "blocked", "invalid_configuration_or_input")
    try:
        result = bridge(policy, payload, os.environ if environment is None else environment)
    except FileNotFoundError:
        return report(capability, "unavailable", "anvil_unavailable")
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        # Once dispatched, egress may have occurred even if stdout was lost.
        return report(capability, "unavailable", "anvil_bridge_unavailable", started=True)
    try:
        annotation = _annotation(result, capability, value)
    except (ValueError, TypeError, KeyError, RecursionError):
        return report(capability, "invalid_response", "invalid_bridge_response", started=True)
    try:
        if policy_reader() != policy:
            return report(capability, "blocked", "policy_changed", started=annotation["request_started"])
    except (OSError, ValueError, TypeError, RecursionError):
        return report(capability, "blocked", "policy_changed", started=annotation["request_started"])
    return annotation


def _annotation(result, capability, value):
    """Validate the local bridge contract before any advice reaches a consumer."""
    if (type(result) is not dict or set(result) != {"ok", "command", "data"}
            or result.get("ok") is not True or result.get("command") != "jev bridge"):
        raise ValueError("Invalid bridge envelope")
    data = result["data"]
    if (type(data) is not dict or data.get("schema") != "anvil.jev.annotation.v1"
            or data.get("provider") != "typesafe" or data.get("model") != MODEL
            or data.get("capability") != capability
            or data.get("status") not in {"disabled", "blocked", "unavailable", "invalid_response", "completed"}):
        raise ValueError("Invalid bridge annotation")
    if any(type(data.get(key)) is not bool for key in ("requested", "used", "request_started")):
        raise ValueError("Invalid bridge state")
    if data["used"] != (data["status"] == "completed") or (data["used"] and not data["request_started"]):
        raise ValueError("Invalid bridge completion")
    allowed = set(report(capability, "disabled", "")) - {"advisory"}
    if set(data) - allowed or not re.fullmatch(r"[a-z0-9_]{0,96}", data.get("reason") or ""):
        raise ValueError("Invalid bridge fields")
    for key in ("rubric_digest", "input_digest"):
        if data.get(key) is not None and not re.fullmatch(r"[a-f0-9]{64}", data[key]):
            raise ValueError("Invalid bridge digest")
    elapsed = data.get("elapsed_ms")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("Invalid bridge timing")
    if type(data.get("answers")) is not dict or type(data.get("usage")) is not dict:
        raise ValueError("Invalid bridge answers")
    usage = data["usage"]
    if usage and (set(usage) != {"input_tokens", "output_tokens"}
                  or any(type(number) is not int or number < 0 for number in usage.values())):
        raise ValueError("Invalid bridge usage")
    # Anvil owns rubric validation. The consumer retains only the versioned
    # contract and checks capability answer identities before using ordering.
    if data["used"]:
        if not data.get("input_digest") or not data.get("rubric_digest") or not usage:
            raise ValueError("Missing completed provenance")
        if data["input_digest"] != digest(value):
            raise ValueError("Bridge input changed")
        _validate_answers(data["answers"], capability, value)
    elif data["answers"]:
        raise ValueError("Incomplete advice has answers")
    return data | {"advisory": True}


def _validate_answers(answers, capability, value):
    # Kept deliberately separate from the provider response parser in Anvil.
    expected = {item["id"] for item in value.get("candidates", [])} if capability == "context_ranking" else {
        "selection" if capability == "skill_suggestion" else "category" if capability == "incident_triage" else "intent"}
    if set(answers) != expected:
        raise ValueError("Invalid answer identities")
    for key, answer in answers.items():
        kind = "score" if capability == "context_ranking" else "choice"
        if type(answer) is not dict or set(answer) != {"type", kind, "confidence", "probabilities"} or answer["type"] != kind:
            raise ValueError("Invalid typed answer")
        choice = answer[kind]
        if capability == "context_ranking":
            if type(choice) not in (int, float) or not math.isfinite(choice) or not 0 <= choice <= 2:
                raise ValueError("Invalid relevance score")
            choices = {"0", "1", "2"}
        else:
            choices = ({item["id"] for item in value.get("candidates", [])} | {"none"}) if capability == "skill_suggestion" else set(INCIDENT_CHECKS) if capability == "incident_triage" else {"conversation", "read_only_information", "operational_change_request", "unclear", "unsupported"}
            if type(choice) is not str or choice not in choices:
                raise ValueError("Invalid advisory choice")
        probabilities = answer["probabilities"]
        if type(probabilities) is not dict or set(probabilities) != choices:
            raise ValueError("Invalid advisory probability labels")
        if any(type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 1
               for number in [answer["confidence"], *probabilities.values()]):
            raise ValueError("Invalid advisory probability")
        if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.02):
            raise ValueError("Invalid advisory probability sum")


def advice_view(annotation, value):
    """Local templates and stable optional ordering; never executable actions."""
    result = {"annotation": annotation}
    if annotation["capability"] == "context_ranking":
        baseline = [item["id"] for item in value.get("candidates", [])]
        order = baseline[:]
        if annotation["used"]:
            order.sort(key=lambda key: -annotation["answers"][key]["score"])
        result.update(baseline=baseline, order=order)
    elif annotation["used"] and annotation["capability"] == "incident_triage":
        result["next_check"] = INCIDENT_CHECKS[annotation["answers"]["category"]["choice"]]
    return result
