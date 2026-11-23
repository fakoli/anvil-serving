"""Hold or release one Connect resource at its local connector listener.

A hold is one IPv4 ``iptables`` OUTPUT rule that resets TCP connections to the
resource's declared ``127.0.0.1`` connector port, so the public path fails fast
while the origin is under maintenance. Each rule carries the comment
``anvil-connect-hold:<resource>``; release deletes only that exact rule.
Holds are runtime firewall state: they do not survive a reboot.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess

from ..operator_output import UsageError
from .config import read_manifest
from .recovery import _path

COMMENT_PREFIX = "anvil-connect-hold:"
_BLOCKING = frozenset(("REJECT", "DROP"))
_LOOPBACK = frozenset(("127.0.0.1", "127.0.0.1/32", "127.0.0.0/8"))


class HoldError(RuntimeError):
    """A firewall read or change failed; the message names what to inspect."""


def _iptables(*args: str) -> str:
    binary = shutil.which("iptables")
    if binary is None:
        raise HoldError("iptables is not installed; a Connect hold needs the IPv4 firewall.")
    result = subprocess.run([binary, "-w", "5", *args], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise HoldError("iptables " + " ".join(args[:2]) + " failed: " + result.stderr.strip()[:200])
    return result.stdout


def _rule(resource: str, port: int) -> list[str]:
    # Matches iptables -S readback order, so a hold can be verified token by token.
    return ["-d", "127.0.0.1/32", "-p", "tcp", "-m", "tcp", "--dport", str(port),
            "-m", "comment", "--comment", COMMENT_PREFIX + resource,
            "-j", "REJECT", "--reject-with", "tcp-reset"]


def _listeners(manifest: str) -> dict[str, int]:
    """Map each connector resource ID to its declared loopback listener port."""
    data = read_manifest(_path(manifest))
    ports = {}
    for connector in data.get("connectors", []):
        for resource in connector["resources"]:
            envelope = resource["envelope"]
            # The manifest parser already restricts listen to 127.0.0.1:PORT.
            ports[envelope["rule"]["id"]] = int(envelope["listen"].rsplit(":", 1)[1])
    return ports


def _ports(tokens: list[str]) -> set[int]:
    found = set()
    for flag, value in zip(tokens, tokens[1:]):
        if flag in {"--dport", "--dports", "--destination-port", "--destination-ports"}:
            for part in value.split(","):
                low, _, high = part.partition(":")
                if low.isdigit() and (not high or high.isdigit()):
                    found.update(range(int(low), int(high or low) + 1))
    return found


def _value(tokens: list[str], flag: str) -> str | None:
    return tokens[tokens.index(flag) + 1] if flag in tokens[:-1] else None


def _observe(port: int, resource: str) -> dict:
    """Read the filter table once: this resource's own hold and any other blocking rule."""
    rules = [shlex.split(line) for line in _iptables("-S").splitlines() if line.startswith("-A ")]
    output = [tokens for tokens in rules if tokens[1] == "OUTPUT"]
    comment = COMMENT_PREFIX + resource
    owned = [i for i, tokens in enumerate(output) if _value(tokens, "--comment") == comment]
    if len(owned) > 1:
        raise HoldError(f"More than one {comment} rule is in OUTPUT; remove the duplicates with iptables -D.")
    others = []
    for tokens in rules:
        if (_value(tokens, "-j") in _BLOCKING and port in _ports(tokens)
                and _value(tokens, "-d") in _LOOPBACK | {None}
                and _value(tokens, "--comment") != comment):
            others.append(shlex.join(tokens))
    state = {"held": bool(owned), "other_blocking_rules": others}
    if owned:
        position = owned[0]
        tokens = output[position]
        state["rule"] = tokens[2:]
        state["matches_declaration"] = tokens[2:] == _rule(resource, port)
        # Only other Connect holds may sit above it; anything else could accept first.
        state["first_in_output"] = all((_value(t, "--comment") or "").startswith(COMMENT_PREFIX)
                                       for t in output[:position])
    return state


def operate(action: str, manifest: str, resource: str | None = None, *, apply: bool = False) -> dict:
    if os.geteuid() != 0:
        raise UsageError("Run connect hold with sudo; it reads and changes the host firewall.",
                         code="connect_hold_root_required")
    ports = _listeners(manifest)
    if resource is not None and resource not in ports:
        raise UsageError("Unknown connector resource; run connect hold status to list declared IDs.",
                         code="connect_hold_unknown_resource")
    if action == "status":
        selected = [resource] if resource else sorted(ports)
        rows = [{"resource": name, "listen": f"127.0.0.1:{ports[name]}", **_observe(ports[name], name)}
                for name in selected]
        return {"holds": rows, "persistent": False, "read_only": True,
                "note": "other_blocking_rules lists firewall rules this command does not own that "
                        "still block a listener; remove them with the tool that added them."}
    port = ports[resource]
    before = _observe(port, resource)
    if action == "apply" and before["held"] and not before["matches_declaration"]:
        raise HoldError("The existing hold no longer matches the declared listener; release it first.")
    change = (action == "apply") != before["held"]
    if apply and change:
        if action == "apply":
            _iptables("-I", "OUTPUT", "1", *_rule(resource, port))
        else:
            _iptables("-D", "OUTPUT", *before["rule"])
    after = _observe(port, resource) if apply else before
    if apply and after["held"] != (action == "apply"):
        raise HoldError("The firewall readback does not show the requested hold state.")
    return {"action": action, "resource": resource, "listen": f"127.0.0.1:{port}",
            "held_before": before["held"], "held_after": after["held"] if apply else before["held"],
            "changed": bool(apply and change), "would_change": change, "confirmed": apply,
            "other_blocking_rules": after["other_blocking_rules"], "persistent": False}
