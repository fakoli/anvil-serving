"""A Connect hold must block exactly one declared listener and release only itself."""
import shlex

import pytest

from anvil_serving.connect import ingress_hold, manage
from anvil_serving.connect.cli import dispatch

UPGRADE_HOLD = ("-A OUTPUT -d 127.0.0.1/32 -p tcp -m multiport --dports 18087,18088 "
                "-m comment --comment upgrade-hold -j REJECT --reject-with tcp-reset")
UNRELATED = "-A OUTPUT -d 127.0.0.1/32 -p tcp -m tcp --dport 18099 -j REJECT --reject-with tcp-reset"


class Firewall:
    def __init__(self, *rules):
        self.rules = list(rules)
        self.changes = []

    def __call__(self, *args):
        if args == ("-S",):
            return "-P OUTPUT ACCEPT\n" + "".join(rule + "\n" for rule in self.rules)
        self.changes.append(args)
        operation, chain, *rest = args
        if operation == "-I":
            self.rules.insert(int(rest[0]) - 1, shlex.join(["-A", chain, *rest[1:]]))
        elif operation == "-D":
            self.rules.remove(shlex.join(["-A", chain, *rest]))
        return ""


@pytest.fixture
def firewall(monkeypatch):
    value = Firewall(UNRELATED)
    manifest = {"connectors": [{"resources": [
        {"envelope": {"rule": {"id": "memory-ui"}, "listen": "127.0.0.1:18088"}},
        {"envelope": {"rule": {"id": "memory-api"}, "listen": "127.0.0.1:18087"}},
    ]}]}
    monkeypatch.setattr(ingress_hold, "read_manifest", lambda _path: manifest)
    monkeypatch.setattr(ingress_hold, "_iptables", value)
    monkeypatch.setattr(ingress_hold.os, "geteuid", lambda: 0)
    monkeypatch.setattr(manage, "supported_platform", lambda: True)
    return value


def test_status_names_a_foreign_rule_blocking_a_listener(firewall):
    firewall.rules.insert(0, UPGRADE_HOLD)
    result = dispatch(["hold", "status"])
    assert result.error is None
    rows = {row["resource"]: row for row in result.data["holds"]}
    assert rows["memory-ui"]["listen"] == "127.0.0.1:18088"
    assert rows["memory-ui"]["held"] is False
    assert rows["memory-ui"]["other_blocking_rules"] == [UPGRADE_HOLD]
    assert rows["memory-api"]["other_blocking_rules"] == [UPGRADE_HOLD]
    assert result.data["persistent"] is False and firewall.changes == []


def test_apply_previews_then_holds_and_release_removes_only_its_rule(firewall):
    preview = dispatch(["hold", "apply", "--resource", "memory-ui"])
    assert preview.data["would_change"] is True and preview.data["changed"] is False
    assert firewall.changes == []

    applied = dispatch(["hold", "apply", "--resource", "memory-ui", "--confirm"])
    assert applied.error is None and applied.data["held_after"] is True and applied.data["changed"] is True
    assert firewall.rules[0] == shlex.join(["-A", "OUTPUT", *ingress_hold._rule("memory-ui", 18088)])
    status = dispatch(["hold", "status", "--resource", "memory-ui"]).data["holds"][0]
    assert status["matches_declaration"] is True and status["first_in_output"] is True
    assert status["other_blocking_rules"] == []

    assert dispatch(["hold", "apply", "--resource", "memory-ui", "--confirm"]).data["changed"] is False
    released = dispatch(["hold", "release", "--resource", "memory-ui", "--confirm"])
    assert released.data["held_after"] is False and released.data["changed"] is True
    assert firewall.rules == [UNRELATED]
    assert dispatch(["hold", "release", "--resource", "memory-ui", "--confirm"]).data["changed"] is False


def test_hold_behind_an_accept_rule_is_reported_as_not_first(firewall):
    firewall.rules.insert(0, "-A OUTPUT -o lo -j ACCEPT")
    dispatch(["hold", "apply", "--resource", "memory-api", "--confirm"])
    firewall.rules.insert(0, firewall.rules.pop(1))  # another tool inserted its rule above ours
    status = dispatch(["hold", "status", "--resource", "memory-api"]).data["holds"][0]
    assert status["held"] is True and status["first_in_output"] is False


@pytest.mark.parametrize("args,code", [
    (["hold", "apply", "--resource", "unknown", "--confirm"], "connect_hold_unknown_resource"),
    (["hold", "apply", "--confirm"], None),
])
def test_invalid_requests_change_nothing(firewall, args, code):
    result = dispatch(args)
    assert result.error is not None and firewall.changes == []
    if code:
        assert result.error.code == code


def test_duplicate_owned_rules_and_non_root_are_refused(firewall, monkeypatch):
    rule = shlex.join(["-A", "OUTPUT", *ingress_hold._rule("memory-ui", 18088)])
    firewall.rules[:0] = [rule, rule]
    result = dispatch(["hold", "release", "--resource", "memory-ui", "--confirm"])
    assert result.error.code == "connect_hold_failed" and firewall.changes == []
    monkeypatch.setattr(ingress_hold.os, "geteuid", lambda: 1000)
    assert dispatch(["hold", "status"]).error.code == "connect_hold_root_required"
