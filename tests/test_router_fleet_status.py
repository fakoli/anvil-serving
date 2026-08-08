"""`router fleet-status` — is every configured capability actually served?

Feature 3 of docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md. On 2026-08-08 the router
advertised three voice/audio routes whose backing serves had been off for hours
and no surface reported it.
"""
import json
import textwrap

from anvil_serving import router_manage
from anvil_serving.router import config as router_config


def _config(tmp_path, body):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return router_config.load(str(path))


_TWO_TIERS = """
    [router]
    [[router.tiers]]
    id = "primary-local"
    base_url = "http://127.0.0.1:30002/v1"
    model = "m-primary"
    dialect = "openai"
    context_limit = 4096
    privacy = "local"
    tool_support = true
    auth_env = "ANVIL_PRIMARY_LOCAL_KEY"
    health_path = "/health"

    [[router.tiers]]
    id = "omni-local"
    base_url = "http://127.0.0.1:30003/v1"
    model = "m-omni"
    dialect = "openai"
    context_limit = 4096
    privacy = "local"
    tool_support = true
    auth_env = "ANVIL_OMNI_LOCAL_KEY"
    health_path = "/health"

    [router.model_routes]
    llm.primary = "primary-local"
    vision.ocr = "omni-local"
"""


def _probe_map(mapping, default=(False, "URLError")):
    def _probe(url, timeout=4.0):
        for fragment, result in mapping.items():
            if fragment in url:
                return result
        return default
    return _probe


def test_reports_each_alias_with_its_backing_endpoint(tmp_path):
    config = _config(tmp_path, _TWO_TIERS)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 200"),
                                   ":30003": (True, "HTTP 200")}))
    assert report["checked"] == 2
    assert report["unreachable"] == 0
    assert report["unreachable_aliases"] == []
    assert {r["name"] for r in report["rows"]} == {"llm.primary", "vision.ocr"}


def test_unreachable_alias_is_named(tmp_path):
    # The incident: a configured alias whose backing serve is simply not there.
    config = _config(tmp_path, _TWO_TIERS)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 200")}))
    assert report["unreachable"] == 1
    assert report["unreachable_aliases"] == ["vision.ocr"]


def test_authenticated_endpoint_counts_as_reachable(tmp_path):
    # A 401 means something IS serving and is asking for a token. Treating it
    # as down would report every authenticated tier as broken.
    config = _config(tmp_path, _TWO_TIERS)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 401"),
                                   ":30003": (True, "HTTP 200")}))
    assert report["unreachable_aliases"] == []


def test_container_relative_host_is_translated_and_reported(tmp_path):
    # The router runs in a container, so its config names the Docker host as
    # host.docker.internal, which does not resolve on the host itself. Probing
    # it verbatim would report a healthy serve as unreachable.
    config = _config(tmp_path, _TWO_TIERS.replace(
        "http://127.0.0.1:30002/v1", "http://host.docker.internal:30002/v1"))
    probed = []

    def _probe(url, timeout=4.0):
        probed.append(url)
        return True, "HTTP 200"

    report = router_manage.fleet_status(config, _probe=_probe)

    assert all("host.docker.internal" not in url for url in probed)
    assert any(url.startswith("http://127.0.0.1:30002/") for url in probed)
    primary = next(r for r in report["rows"] if r["name"] == "llm.primary")
    assert "host-relative loopback" in primary["detail"]
    # The declared host stays visible so the translation is never silent.
    assert primary["host"] == "host.docker.internal"


def test_localhost_is_not_substituted(tmp_path):
    # CLAUDE.md: 127.0.0.1 is host-relative; `localhost` must never appear.
    config = _config(tmp_path, _TWO_TIERS.replace(
        "http://127.0.0.1:30002/v1", "http://host.docker.internal:30002/v1"))
    urls = []
    router_manage.fleet_status(
        config, _probe=lambda u, timeout=4.0: (urls.append(u), (True, "ok"))[1])
    assert all("localhost" not in u for u in urls)


def test_audio_routes_are_checked(tmp_path):
    config = _config(tmp_path, _TWO_TIERS + """
    [[router.audio_routes]]
    id = "local-stt"
    purpose = "stt"
    model = "tdt"
    base_url = "http://127.0.0.1:30010/v1"
    default = true
    """)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 200"),
                                   ":30003": (True, "HTTP 200")}))
    audio = [r for r in report["rows"] if r["kind"] == "audio"]
    assert len(audio) == 1
    assert audio[0]["reachable"] is False


def test_cmd_exits_nonzero_only_for_unreachable_aliases(tmp_path, capsys):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(_TWO_TIERS), encoding="utf-8")

    rc = router_manage.cmd_fleet_status(
        str(path), _probe=_probe_map({":30002": (True, "HTTP 200"),
                                      ":30003": (True, "HTTP 200")}))
    assert rc == 0
    assert "0 unreachable" in capsys.readouterr().out

    rc = router_manage.cmd_fleet_status(
        str(path), _probe=_probe_map({":30002": (True, "HTTP 200")}))
    assert rc == 1
    assert "vision.ocr" in capsys.readouterr().out


def test_cmd_json_is_machine_readable(tmp_path, capsys):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(_TWO_TIERS), encoding="utf-8")
    router_manage.cmd_fleet_status(
        str(path), as_json=True,
        _probe=_probe_map({":30002": (True, "HTTP 200"), ":30003": (True, "HTTP 200")}))
    report = json.loads(capsys.readouterr().out)
    assert report["checked"] == 2
    assert report["config"] == str(path)


def test_missing_config_is_a_usage_error(capsys):
    rc = router_manage.cmd_fleet_status("/nonexistent/router.toml")
    assert rc == 2
    assert "could not load router config" in capsys.readouterr().err
