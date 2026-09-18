"""Runnable design examples, NOT production implementations or operational tools.

Run from any directory: python3 <this-file>
No network, subprocess, filesystem mutation, credentials or live State access.
Move reviewed logic into existing owner modules; never import docs in production.
"""
from pathlib import Path
import sys

# Only for running this repository-local documentation self-check.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from anvil_serving.observability.dashboard.contracts import (  # noqa: E402
    ObservatoryError, digest, identifier, strict_json,
)


# T01: raw output stays in a private transport attribute, never exception args.
class BoundedCommandFailure(ObservatoryError):
    def __init__(self, stdout: bytes):
        super().__init__("project_source_unavailable", "Anvil could not complete this read.", 409)
        self.stdout = stdout  # Never serialize/log exception attributes.


# T01: map a bounded CLI failure to fixed public text, not upstream message text.
def project_read_error(raw: bytes) -> ObservatoryError:
    fallback = ObservatoryError(
        "project_source_unavailable", "Anvil could not complete this read.", 409,
    )
    if type(raw) is not bytes or len(raw) > 4 * 1024 * 1024:
        return fallback
    try:
        envelope = strict_json(raw)
    except ObservatoryError:
        return fallback
    if type(envelope) is not dict or envelope.get("ok") is not False:
        return fallback
    error = envelope.get("error")
    if type(error) is not dict or error.get("schema_id") != "anvil.state.read-error.v1":
        return fallback
    if error.get("code") == "projection_not_converged":
        return ObservatoryError(
            "project_projection_not_converged",
            "The project State projection is inconsistent. An operator must inspect State health before this plan can be read.",
            409,
        )
    return fallback


# T03: pure lookup only. This does NOT enforce filesystem/tool access.
def primary_root(project: dict) -> dict:
    roots = project.get("roots")
    if type(roots) is not list or not 1 <= len(roots) <= 16:
        raise ValueError("Declare between 1 and 16 project roots")
    primary_id = identifier(project.get("primary_root_id"))
    seen = set()
    for root in roots:
        if type(root) is not dict:
            raise ValueError("Root must be an object")
        root_id = identifier(root.get("id"))
        if root_id in seen:
            raise ValueError("Duplicate root ID")
        seen.add(root_id)
    if primary_id not in seen:
        raise ValueError("Primary must name a declared root")
    return next(root for root in roots if root["id"] == primary_id)


# T06: namespaced identity; a native ID is not globally unique.
# Authorization belongs BEFORE projection and is deliberately not simulated here.
def projected_run_id(owner_id: str, source: str, native_id: str) -> str:
    if type(native_id) is not str or not native_id:
        raise ValueError("Native ID must be nonempty text")
    try:
        encoded = native_id.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Native ID must be valid UTF-8") from None
    if len(encoded) > 1024 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in native_id):
        raise ValueError("Native ID exceeds its bound or contains controls")
    return "run-" + digest({
        "owner_id": identifier(owner_id),
        "source": identifier(source),
        "native_id": native_id,
    })


def self_check():
    raw = b'{"ok":false,"error":{"schema_id":"anvil.state.read-error.v1","code":"projection_not_converged","message":"DO_NOT_EXPOSE_PRIVATE_DETAILS"}}'
    failure = BoundedCommandFailure(raw)
    assert "DO_NOT_EXPOSE" not in str(failure)
    assert "DO_NOT_EXPOSE" not in repr(failure)
    assert raw not in failure.args
    mapped = project_read_error(failure.stdout)
    assert mapped.code == "project_projection_not_converged"
    assert "DO_NOT_EXPOSE" not in mapped.message
    for invalid in (b'not-json', b'[]', b'{"ok":false,"error":[]}',
                    b'{"ok":true,"ok":false}', b'x' * (4 * 1024 * 1024 + 1)):
        assert project_read_error(invalid).code == "project_source_unavailable"
    unknown = raw.replace(b'projection_not_converged', b'unknown_private_error')
    assert project_read_error(unknown).code == "project_source_unavailable"

    roots = [{"id": "primary"}, {"id": "reference"}]
    assert primary_root({"primary_root_id": "primary", "roots": roots}) is roots[0]
    for project in ({"primary_root_id": "absent", "roots": roots},
                    {"primary_root_id": "primary", "roots": roots + [roots[0]]},
                    {"primary_root_id": "primary", "roots": []}):
        try:
            primary_root(project)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid root declaration was accepted")
    key = projected_run_id("owner-a", "benchmark", "job-7")
    assert key == projected_run_id("owner-a", "benchmark", "job-7")
    assert key != projected_run_id("owner-b", "benchmark", "job-7")
    assert key != projected_run_id("owner-a", "operation", "job-7")
    assert identifier(key) == key
    opaque = projected_run_id("owner-a", "benchmark", "jobs/2026/7")
    assert opaque != projected_run_id("owner-a", "benchmark", "jobs:2026:7")
    for native_id in ("", "bad\r\nheader", "x" * 1025, "\ud800"):
        try:
            projected_run_id("owner-a", "benchmark", native_id)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid native ID was accepted")
    print("PASS: typed-error mapping, primary-root invariant, namespaced run identity")


if __name__ == "__main__":
    self_check()
