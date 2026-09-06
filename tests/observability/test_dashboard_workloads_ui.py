from __future__ import annotations

import json
import os
import shutil
import subprocess
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path

import pytest


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.labels: set[str] = set()
        self.scripts: list[dict[str, str | None]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        if identifier := values.get("id"):
            self.by_id[identifier] = (tag, values)
        if tag == "label" and values.get("for"):
            self.labels.add(values["for"])
        if tag == "script":
            self.scripts.append(values)


def _static_path(name: str) -> Path:
    return Path(str(files("anvil_serving.observability.dashboard.static").joinpath(name)))


def test_packaged_workload_script_executes_in_bounded_node_vm() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.fail(
            "Node.js is required as a development runtime for the executable "
            "workload dashboard tests"
        )
    script = Path(__file__).with_name("dashboard_workloads_ui.cjs")
    environment = {
        key: os.environ[key]
        for key in ("PATH", "PATHEXT", "SystemRoot", "WINDIR", "TEMP", "TMP")
        if key in os.environ
    }
    try:
        completed = subprocess.run(
            [node, str(script), str(_static_path("workloads.js"))],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"Node workload dashboard harness could not run: {type(exc).__name__}")
    assert completed.returncode == 0, (
        "Node workload dashboard harness failed\n"
        f"stdout tail:\n{completed.stdout[-2000:]}\n"
        f"stderr tail:\n{completed.stderr[-4000:]}"
    )
    payload = json.loads(completed.stdout)
    assert payload == {
        "ok": True,
        "scenarios": [
            "testHiddenAndCanonicalRendering",
            "testStatusesAndOmissions",
            "testFiltersAndValidationFailures",
            "testMalformedResponsesAreFixed",
            "testCadenceTimeoutAndLateCompletion",
            "testGenerationInvalidationAndReconnect",
            "testFilterChangeInvalidatesOldGeneration",
            "testAuthorizationAndRetryRelease",
        ],
    }
    assert completed.stderr == ""


def test_workload_panel_markup_has_closed_accessible_controls() -> None:
    html = _static_path("index.html").read_text(encoding="utf-8")
    parser = _Markup()
    parser.feed(html)

    assert {"src": "/workloads.js", "defer": None} in parser.scripts
    assert parser.by_id["workloads-tab"] == (
        "button",
        {
            "id": "workloads-tab",
            "class": "tab",
            "type": "button",
            "role": "tab",
            "aria-selected": "false",
            "aria-controls": "workloads",
            "data-tab": "workloads",
            "tabindex": "-1",
        },
    )
    assert parser.by_id["workloads"][1]["role"] == "tabpanel"
    assert parser.by_id["workloads"][1]["aria-labelledby"] == "workloads-tab"
    assert "hidden" in parser.by_id["workloads"][1]

    controls = {
        "workload-owner",
        "workload-kind",
        "workload-state",
        "workload-host",
        "workload-active",
        "workload-recent",
        "workload-limit",
    }
    assert controls <= parser.labels
    assert parser.by_id["workload-token"][1] == {
        "id": "workload-token",
        "type": "password",
        "required": None,
        "minlength": "16",
        "maxlength": "4096",
        "autocomplete": "off",
        "spellcheck": "false",
    }
    assert parser.by_id["workload-disconnect"][1]["type"] == "button"
    assert "disabled" in parser.by_id["workload-disconnect"][1]
    assert parser.by_id["workload-status"][1]["role"] == "status"
    assert parser.by_id["workload-status"][1]["aria-live"] == "polite"
    assert parser.by_id["workload-results"][1]["aria-label"] == "Workload observations"
    assert parser.by_id["workload-host"][1]["maxlength"] == "64"
    assert parser.by_id["workload-recent"][1]["value"] == "3600"
    assert parser.by_id["workload-recent"][1]["max"] == "86400"
    assert parser.by_id["workload-limit"][1]["value"] == "200"
    assert parser.by_id["workload-limit"][1]["max"] == "1000"

    workload_markup = html.split('<section id="workloads"', 1)[1].split("</section>", 1)[0]
    assert "window.AnvilWorkloads?.setVisible(name==='workloads')" in html
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert key in html
    for action in ("start", "stop", "restart", "delete", "promote", "rollback"):
        assert f">{action}<" not in workload_markup.lower()
