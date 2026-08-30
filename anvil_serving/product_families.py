"""Canonical Anvil Serving product families and operator journeys.

This module is deliberately independent of the command registry.  The CLI,
manifest, documentation checks, and other callers can consume the same bounded
catalog without importing operational handlers or reading operator state.
"""

from __future__ import annotations

from dataclasses import dataclass


CATALOG_SCHEMA_VERSION = "anvil-serving.product-families/v1"


def _umbrella_data() -> dict[str, str]:
    """Return the stable umbrella object shared by every catalog response."""
    return {
        "name": "Anvil Serving",
        "promise": (
            "Operate, qualify, and expose local AI capabilities through explicit, "
            "reviewable contracts."
        ),
        "boundary": (
            "Anvil Serving never infers a caller's intent, silently substitutes a model, "
            "auto-promotes evidence, or moves an operation outside declared ownership."
        ),
    }


@dataclass(frozen=True)
class JourneyStep:
    """One ordered, user-visible step through a product family."""

    stage: str
    intent: str
    cli: str
    outcome: str

    def as_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "intent": self.intent,
            "cli": self.cli,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class ProductFamily:
    """One stable product boundary inside the Anvil Serving umbrella."""

    id: str
    name: str
    promise: str
    boundary: str
    commands: tuple[str, ...]
    docs_anchor: str
    journey: tuple[JourneyStep, ...]

    def as_dict(self, *, include_journey: bool = True) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "promise": self.promise,
            "boundary": self.boundary,
            "commands": list(self.commands),
            "docs_anchor": self.docs_anchor,
        }
        if include_journey:
            data["journey"] = [step.as_dict() for step in self.journey]
        return data


PRODUCT_FAMILIES = (
    ProductFamily(
        id="model-serving",
        name="Model Serving",
        promise="Discover, pin, start, inspect, switch, and stop local model serves.",
        boundary=(
            "Owns model artifacts, recipes, serve lifecycle, and reservations; it does "
            "not choose a capability for callers or promote a candidate automatically."
        ),
        commands=("init", "models", "serves"),
        docs_anchor="docs/PRODUCT-FAMILIES.md#model-serving",
        journey=(
            JourneyStep(
                "initialize",
                "Create the private operator scaffold.",
                "anvil-serving init --out-dir <OPERATOR_HOME>",
                "A reviewable host-local configuration scaffold exists outside the public repo.",
            ),
            JourneyStep(
                "select",
                "Inspect pinned, reproducible model recipes.",
                "anvil-serving models recipes list",
                "Candidate identities and activation roles are visible before lifecycle work.",
            ),
            JourneyStep(
                "preview",
                "Validate a declared serve group without starting it.",
                "anvil-serving serves up --group <GROUP> --dry-run",
                "The exact lifecycle and reservation plan is available for review.",
            ),
            JourneyStep(
                "apply",
                "Start only the reviewed serve group.",
                "anvil-serving serves up --group <GROUP> --confirm",
                "Managed services start under the declared manifest and ownership contract.",
            ),
            JourneyStep(
                "observe",
                "Inspect managed serve state.",
                "anvil-serving serves status",
                "Readiness and ownership are visible without changing routing or promotion state.",
            ),
        ),
    ),
    ProductFamily(
        id="capability-gateway",
        name="Capability Gateway",
        promise="Expose explicit capability aliases through one authenticated protocol boundary.",
        boundary=(
            "Owns auth, exact alias resolution, dialect translation, readiness, admission, "
            "streaming, and relay; it never classifies intent, selects a model semantically, "
            "or falls back to another tier."
        ),
        commands=("router",),
        docs_anchor="docs/PRODUCT-FAMILIES.md#capability-gateway",
        journey=(
            JourneyStep(
                "preview",
                "Review router lifecycle against the declared topology.",
                "anvil-serving router up --dry-run",
                "The exact router service and configuration action is shown without mutation.",
            ),
            JourneyStep(
                "apply",
                "Start the authenticated gateway.",
                "anvil-serving router up --confirm",
                "The deployed router serves only explicitly configured routes.",
            ),
            JourneyStep(
                "observe",
                "Inspect the installed router and backing capability state.",
                "anvil-serving router status",
                "Router health is visible without changing alias bindings.",
            ),
            JourneyStep(
                "verify",
                "Exercise the caller-visible route contract.",
                "anvil-serving eval routed --help",
                "The routed acceptance workflow and required evidence inputs are explicit.",
            ),
        ),
    ),
    ProductFamily(
        id="evaluation-evidence",
        name="Evaluation & Evidence",
        promise="Prove functional compatibility and record comparison-safe benchmark evidence.",
        boundary=(
            "Owns preflight, routed acceptance, benchmarks, and durable evidence; results never "
            "change a serve, route, or promotion without a separate human-reviewed transaction."
        ),
        commands=("eval",),
        docs_anchor="docs/PRODUCT-FAMILIES.md#evaluation-evidence",
        journey=(
            JourneyStep(
                "plan",
                "Resolve a bounded functional gate without sending requests.",
                "anvil-serving eval preflight --tier <TIER> --dry-run",
                "Endpoint, model, checks, and output contract are validated before execution.",
            ),
            JourneyStep(
                "qualify",
                "Run the independently defined endpoint checks.",
                "anvil-serving eval preflight --tier <TIER> --confirm",
                "A concrete served configuration receives pass/fail evidence.",
            ),
            JourneyStep(
                "measure",
                "Choose the benchmark family appropriate to the claim.",
                "anvil-serving eval benchmark --help",
                "Capacity, quality, context, agentic, SWE, or multimodal evidence stays explicit.",
            ),
            JourneyStep(
                "review",
                "Inspect retained evidence before any promotion decision.",
                "anvil-serving eval benchmark evidence list",
                "Recorded runs can be compared without mutating serving state.",
            ),
        ),
    ),
    ProductFamily(
        id="anvil-voice",
        name="Anvil Voice",
        promise="Operate qualified STT, TTS, and realtime voice paths as one explicit domain.",
        boundary=(
            "Owns audio serve and realtime-proxy lifecycle plus voice qualification; it does not "
            "move models to undeclared hosts or hide split-host ownership behind fallback."
        ),
        commands=("voice",),
        docs_anchor="docs/PRODUCT-FAMILIES.md#anvil-voice",
        journey=(
            JourneyStep(
                "validate",
                "Validate an explicit voice profile.",
                "anvil-serving voice profiles validate --profile <PROFILE>",
                "Audio routes and lifecycle ownership are checked before startup.",
            ),
            JourneyStep(
                "preview-audio",
                "Review STT/TTS lifecycle on the resource owner.",
                "anvil-serving voice audio up --profile <PROFILE> --dry-run",
                "The bounded audio-serve action is visible without starting models.",
            ),
            JourneyStep(
                "preview-proxy",
                "Review the realtime proxy lifecycle separately.",
                "anvil-serving voice proxy up --profile <PROFILE> --dry-run",
                "Split-host proxy ownership remains explicit.",
            ),
            JourneyStep(
                "observe",
                "Inspect the running audio path.",
                "anvil-serving voice audio status --profile <PROFILE>",
                "Managed STT/TTS status is reported without changing the route.",
            ),
            JourneyStep(
                "qualify",
                "Select the voice benchmark appropriate to the claim.",
                "anvil-serving voice benchmark --help",
                "Latency and quality claims require retained, independent evidence.",
            ),
        ),
    ),
    ProductFamily(
        id="anvil-media",
        name="Anvil Media",
        promise="Run bounded named image and video workflows with durable jobs and artifacts.",
        boundary=(
            "Owns workflow validation, job state, cancellation, reconciliation, qualification, "
            "and opaque artifacts; callers cannot submit raw backend graphs, paths, installs, "
            "placement choices, or fallback lists."
        ),
        commands=("media",),
        docs_anchor="docs/PRODUCT-FAMILIES.md#anvil-media",
        journey=(
            JourneyStep(
                "discover",
                "List named workflows and their qualification-gated availability.",
                "anvil-serving media capabilities",
                "Callers see explicit workflow ids, versions, schemas, profiles, and blockers.",
            ),
            JourneyStep(
                "inventory",
                "Verify exact pinned worker assets before execution.",
                "anvil-serving media bundle inventory <WORKFLOW> --version <VERSION> --models-volume <VOLUME>",
                "Missing or mismatched model assets fail closed without modifying the worker.",
            ),
            JourneyStep(
                "validate",
                "Validate one workflow against its selected backend.",
                "anvil-serving media workflow validate <WORKFLOW> --version <VERSION> --backend-url <URL>",
                "Runtime compatibility is checked without accepting a raw workflow graph.",
            ),
            JourneyStep(
                "preview",
                "Review a bounded submission without creating work.",
                "anvil-serving media workflow run <WORKFLOW> --version <VERSION> --parameters <JSON> --principal <ID> --backend-url <URL> --dry-run",
                "The exact named workflow and parameter binding is available for review.",
            ),
            JourneyStep(
                "observe",
                "Inspect the durable job and its opaque artifact metadata.",
                "anvil-serving media job status <JOB_ID> --principal <ID>",
                "Progress, terminal state, and artifact identity survive caller disconnects.",
            ),
        ),
    ),
    ProductFamily(
        id="control-plane-fleet",
        name="Control Plane & Fleet",
        promise="Resolve ownership, dispatch bounded operations, and expose fleet-wide state.",
        boundary=(
            "Owns topology, controller/MCP dispatch, host repair, client integration, fleet "
            "visibility, and observability; it does not bypass resource owners, embed secrets, "
            "or turn SSH into the normal operation path."
        ),
        commands=(
            "fleet",
            "harness",
            "mcp",
            "controller",
            "host",
            "doctor",
            "upgrade",
            "topology",
            "collectors",
            "dashboard",
            "edge",
            "workbench",
        ),
        docs_anchor="docs/PRODUCT-FAMILIES.md#control-plane-fleet",
        journey=(
            JourneyStep(
                "validate",
                "Validate the public schema and private topology values offline.",
                "anvil-serving topology validate",
                "Ownership and transport declarations are checked before dispatch.",
            ),
            JourneyStep(
                "inspect-host",
                "Inspect the command host through the supported utility surface.",
                "anvil-serving host status",
                "Capacity and runtime state are visible without raw lifecycle commands.",
            ),
            JourneyStep(
                "inspect-controller",
                "Probe the typed remote-operation boundary.",
                "anvil-serving controller status",
                "Authentication and declared operation capabilities fail closed.",
            ),
            JourneyStep(
                "inspect-fleet",
                "Check package parity across declared hosts.",
                "anvil-serving fleet version",
                "Version skew, missing installs, and availability states are reported per host.",
            ),
            JourneyStep(
                "integrate",
                "Inspect agent-facing tools before syncing a client.",
                "anvil-serving mcp tools",
                "The bounded tool catalog is visible without granting lifecycle authority.",
            ),
        ),
    ),
)


_BY_ID = {family.id: family for family in PRODUCT_FAMILIES}
_BY_COMMAND = {command: family.id for family in PRODUCT_FAMILIES for command in family.commands}
_ALIASES = {
    "serving": "model-serving",
    "gateway": "capability-gateway",
    "evaluation": "evaluation-evidence",
    "eval": "evaluation-evidence",
    "voice": "anvil-voice",
    "media": "anvil-media",
    "control": "control-plane-fleet",
    "fleet": "control-plane-fleet",
}


def family_for_id(value: str) -> ProductFamily:
    """Resolve a stable id or documented short alias to one family."""
    normalized = value.strip().casefold()
    family_id = _ALIASES.get(normalized, normalized)
    try:
        return _BY_ID[family_id]
    except KeyError:
        choices = ", ".join(family.id for family in PRODUCT_FAMILIES)
        raise ValueError(f"unknown product family {value!r}; choose one of: {choices}") from None


def family_id_for_command(root_command: str) -> str | None:
    """Return the family id for one root command, or ``None`` for discovery."""
    return _BY_COMMAND.get(root_command)


def family_name_for_command(root_command: str) -> str | None:
    family_id = family_id_for_command(root_command)
    return _BY_ID[family_id].name if family_id is not None else None


def validate_command_coverage(
    root_commands: tuple[str, ...], *, excluded: tuple[str, ...] = ()
) -> None:
    """Fail if the operational root surface is not partitioned exactly once."""
    declared = [command for family in PRODUCT_FAMILIES for command in family.commands]
    duplicates = sorted({command for command in declared if declared.count(command) > 1})
    expected = set(root_commands) - set(excluded)
    actual = set(declared)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if duplicates or missing or unexpected:
        raise ValueError(
            "product-family command coverage mismatch: "
            f"duplicates={duplicates} missing={missing} unexpected={unexpected}"
        )


def catalog_data(*, include_journeys: bool = False) -> dict[str, object]:
    """Return the bounded, deterministic umbrella product catalog."""
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "umbrella": _umbrella_data(),
        "families": [
            family.as_dict(include_journey=include_journeys) for family in PRODUCT_FAMILIES
        ],
    }


def journey_data(family_id: str) -> dict[str, object]:
    """Return one complete ordered user journey."""
    family = family_for_id(family_id)
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "umbrella": _umbrella_data(),
        "family": family.as_dict(include_journey=True),
    }
