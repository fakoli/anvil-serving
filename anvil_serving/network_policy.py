"""Fail-closed network policy for model-serving workloads.

Download jobs are intentionally outside this module: they fetch immutable assets into
managed storage and terminate.  Long-running inference workloads default to an
externally isolated Docker network so engine or model code cannot emit telemetry.
"""
from __future__ import annotations

import json
import subprocess


MODEL_EGRESS_DENY_NETWORK = "anvil-serving-model-egress-denied"
MANAGED_BY_LABEL = "io.anvil-serving.managed-by"
MANAGED_BY_VALUE = "model-egress-policy"
EGRESS_LABEL = "io.anvil-serving.network.egress"
EGRESS_REASON_LABEL = "io.anvil-serving.network.egress-reason"
EGRESS_ROLE_LABEL = "io.anvil-serving.network.egress-role"
POLICIES = frozenset({"allow", "deny"})
GATEWAY_ROLES = frozenset({
    "capability-gateway",
    "media-gateway",
    "voice-gateway",
})


class NetworkPolicyError(ValueError):
    """A workload cannot be proven to satisfy its declared egress policy."""


def normalize_policy(config, *, context):
    """Return ``(policy, reason)`` for a recipe or manifest mapping.

    Absence means ``deny``.  An allow exception must identify one narrowly scoped
    gateway role and carry a durable reason in the same reviewed configuration
    object; a command-line-only escape hatch would be too easy to lose from audit
    history.
    """
    raw = config.get("network_egress", "deny")
    if not isinstance(raw, str) or raw.strip().lower() not in POLICIES:
        raise NetworkPolicyError(
            "%s network_egress must be 'deny' or 'allow'" % context
        )
    policy = raw.strip().lower()
    reason = config.get("network_egress_reason")
    role = config.get("network_egress_role")
    if policy == "allow":
        if not isinstance(role, str) or role.strip().lower() not in GATEWAY_ROLES:
            raise NetworkPolicyError(
                "%s network_egress='allow' requires network_egress_role to be "
                "one of %s" % (context, sorted(GATEWAY_ROLES))
            )
        if not isinstance(reason, str) or not reason.strip():
            raise NetworkPolicyError(
                "%s network_egress='allow' requires a non-empty "
                "network_egress_reason" % context
            )
        reason = reason.strip()
    elif reason is not None or role is not None:
        raise NetworkPolicyError(
            "%s network_egress_role/network_egress_reason are only valid when "
            "network_egress='allow'"
            % context
        )
    return policy, reason


def _network_document(stdout):
    try:
        rows = json.loads(stdout)
        row = rows[0]
        if not isinstance(row, dict):
            raise TypeError
        return row
    except (IndexError, TypeError, ValueError, AttributeError):
        raise NetworkPolicyError(
            "docker returned malformed model-egress network inspection data"
        ) from None


def _validate_managed_network(row):
    labels = row.get("Labels") or {}
    if row.get("Name") != MODEL_EGRESS_DENY_NETWORK:
        raise NetworkPolicyError("docker inspected the wrong model-egress network")
    if row.get("Driver") != "bridge" or row.get("Internal") is not True:
        raise NetworkPolicyError(
            "Docker network %r exists but is not an internal bridge; refusing "
            "a fail-open model start" % MODEL_EGRESS_DENY_NETWORK
        )
    if labels.get(MANAGED_BY_LABEL) != MANAGED_BY_VALUE:
        raise NetworkPolicyError(
            "Docker network %r is not owned by Anvil Serving; refusing to trust "
            "a same-named network" % MODEL_EGRESS_DENY_NETWORK
        )
    return {
        "name": MODEL_EGRESS_DENY_NETWORK,
        "driver": "bridge",
        "internal": True,
        "managed_by": MANAGED_BY_VALUE,
    }


def ensure_model_egress_network(*, _run=subprocess.run):
    """Create or verify the managed internal bridge before a model starts.

    Every successful return is based on a fresh postcondition inspection.  A
    concurrent creator is harmless: a failed create is accepted only if the
    follow-up inspection proves the exact managed invariant.
    """
    inspect_argv = ["docker", "network", "inspect", MODEL_EGRESS_DENY_NETWORK]
    try:
        inspected = _run(
            inspect_argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise NetworkPolicyError(
            "cannot inspect the model-egress Docker network: %s" % exc
        ) from None
    if inspected.returncode == 0:
        return _validate_managed_network(
            _network_document(getattr(inspected, "stdout", ""))
        )

    create_argv = [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        "--label",
        "%s=%s" % (MANAGED_BY_LABEL, MANAGED_BY_VALUE),
        MODEL_EGRESS_DENY_NETWORK,
    ]
    try:
        _run(
            create_argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        verified = _run(
            inspect_argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise NetworkPolicyError(
            "cannot create or verify the model-egress Docker network: %s" % exc
        ) from None
    if verified.returncode != 0:
        raise NetworkPolicyError(
            "cannot verify Docker network %r after create: %s"
            % (
                MODEL_EGRESS_DENY_NETWORK,
                (verified.stderr or verified.stdout or "unknown Docker error").strip(),
            )
        )
    return _validate_managed_network(
        _network_document(getattr(verified, "stdout", ""))
    )


def _service_network_names(service):
    if "network_mode" in service:
        mode = service.get("network_mode")
        if mode == "none":
            return ()
        raise NetworkPolicyError(
            "uses network_mode=%r, which is not externally isolated" % mode
        )
    declared = service.get("networks")
    if declared is None:
        return ("default",)
    if isinstance(declared, dict):
        names = tuple(declared)
    elif isinstance(declared, list):
        names = tuple(declared)
    else:
        raise NetworkPolicyError("has malformed Compose networks")
    if not names:
        return ("default",)
    if not all(isinstance(name, str) and name for name in names):
        raise NetworkPolicyError("has malformed Compose network names")
    return names


def validate_compose_document(
    document,
    service_names,
    *,
    policy="deny",
    reason=None,
    role=None,
):
    """Prove selected effective Compose services satisfy one egress policy."""
    if policy is not None:
        policy, reason = normalize_policy(
            {
                "network_egress": policy,
                **(
                    {"network_egress_reason": reason}
                    if reason is not None else {}
                ),
                **(
                    {"network_egress_role": role}
                    if role is not None else {}
                ),
            },
            context="Compose workload",
        )
    if not isinstance(document, dict):
        raise NetworkPolicyError("effective Compose configuration must be an object")
    services = document.get("services")
    networks = document.get("networks") or {}
    if not isinstance(services, dict) or not isinstance(networks, dict):
        raise NetworkPolicyError("effective Compose services/networks are malformed")
    selected = list(service_names) if service_names else list(services)
    if not selected:
        raise NetworkPolicyError("effective Compose configuration has no selected services")

    results = []
    for name in selected:
        service = services.get(name)
        if not isinstance(service, dict):
            raise NetworkPolicyError(
                "effective Compose configuration does not contain service %r" % name
            )
        service_policy = policy
        service_reason = reason
        service_role = role
        if policy is None:
            labels = service.get("labels") or {}
            if isinstance(labels, list):
                labels = dict(
                    item.split("=", 1) if "=" in item else (item, "")
                    for item in labels if isinstance(item, str)
                )
            if not isinstance(labels, dict):
                raise NetworkPolicyError("service %r has malformed labels" % name)
            service_policy = str(labels.get(EGRESS_LABEL, "deny")).strip().lower()
            service_reason = labels.get(EGRESS_REASON_LABEL)
            service_role = labels.get(EGRESS_ROLE_LABEL)
            service_policy, service_reason = normalize_policy(
                {
                    "network_egress": service_policy,
                    **(
                        {"network_egress_reason": service_reason}
                        if service_reason is not None else {}
                    ),
                    **(
                        {"network_egress_role": service_role}
                        if service_role is not None else {}
                    ),
                },
                context="Compose service %r" % name,
            )
        if service_policy == "allow":
            results.append({
                "service": name,
                "network_egress": "allow",
                "role": str(service_role).strip().lower(),
                "reason": service_reason,
            })
            continue
        if service_policy != "deny":
            raise NetworkPolicyError("unsupported network egress policy %r" % service_policy)
        try:
            attached = _service_network_names(service)
        except NetworkPolicyError as exc:
            raise NetworkPolicyError("Compose service %r %s" % (name, exc)) from None
        for network_name in attached:
            network = networks.get(network_name)
            if not isinstance(network, dict) or network.get("internal") is not True:
                raise NetworkPolicyError(
                    "Compose service %r attaches to network %r without internal=true; "
                    "refusing a fail-open model start" % (name, network_name)
                )
        results.append(
            {
                "service": name,
                "network_egress": "deny",
                "networks": list(attached),
            }
        )
    return results


def compose_config(up, *, env=None, _run=subprocess.run):
    """Resolve one Compose ``up`` argv to its effective JSON document."""
    try:
        compose_index = up.index("compose")
        up_index = up.index("up", compose_index + 1)
    except (AttributeError, ValueError):
        raise NetworkPolicyError("egress enforcement requires docker compose up") from None
    argv = [*up[:up_index], "config", "--format", "json"]
    try:
        kwargs = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if env is not None:
            kwargs["env"] = env
        completed = _run(argv, **kwargs)
    except OSError as exc:
        raise NetworkPolicyError(
            "cannot resolve effective Compose configuration: %s" % exc
        ) from None
    if completed.returncode != 0:
        raise NetworkPolicyError(
            "cannot resolve effective Compose configuration: %s"
            % (
                (completed.stderr or completed.stdout or "unknown Docker Compose error").strip()
            )
        )
    try:
        document = json.loads(completed.stdout)
    except (TypeError, ValueError):
        raise NetworkPolicyError(
            "docker compose config returned malformed JSON"
        ) from None
    return document, argv
