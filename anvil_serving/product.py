"""Read-only CLI projection of the canonical Anvil Serving product map."""

from __future__ import annotations

import argparse
import sys

from .operator_output import CommandResult, UsageError
from .product_families import catalog_data, journey_data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving product",
        description="Discover Anvil Serving product families and ordered user journeys.",
    )
    actions = parser.add_subparsers(dest="action")
    actions.add_parser("families", help="List the six product families and boundaries.")
    journey = actions.add_parser("journey", help="Show one ordered family journey.")
    journey.add_argument("family", help="Stable family id or short alias.")
    return parser


def _families_human(data: dict[str, object]) -> str:
    umbrella = data["umbrella"]
    assert isinstance(umbrella, dict)
    lines = [str(umbrella["name"]), str(umbrella["promise"]), ""]
    families = data["families"]
    assert isinstance(families, list)
    for family in families:
        assert isinstance(family, dict)
        commands = ", ".join(str(item) for item in family["commands"])
        lines.extend(
            (
                f"{family['name']} ({family['id']})",
                f"  {family['promise']}",
                f"  Boundary: {family['boundary']}",
                f"  Commands: {commands}",
                f"  Journey: anvil-serving product journey {family['id']}",
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _journey_human(data: dict[str, object]) -> str:
    family = data["family"]
    assert isinstance(family, dict)
    lines = [
        f"{family['name']} ({family['id']})",
        str(family["promise"]),
        f"Boundary: {family['boundary']}",
        "",
        "Journey:",
    ]
    journey = family["journey"]
    assert isinstance(journey, list)
    for index, step in enumerate(journey, 1):
        assert isinstance(step, dict)
        lines.extend(
            (
                f"  {index}. {step['stage']}: {step['intent']}",
                f"     {step['cli']}",
                f"     Outcome: {step['outcome']}",
            )
        )
    lines.extend(("", f"Docs: {family['docs_anchor']}"))
    return "\n".join(lines) + "\n"


def dispatch(argv=None) -> CommandResult:
    """Return typed product discovery data for the root dispatcher."""
    args = _parser().parse_args(argv)
    if args.action in (None, "families"):
        data = catalog_data()
        return CommandResult(data=data, human_stdout=_families_human(data))
    try:
        data = journey_data(args.family)
    except ValueError as exc:
        error = UsageError(str(exc), code="unknown_product_family")
        return CommandResult(error=error, human_stderr=f"anvil-serving product: {exc}\n")
    return CommandResult(data=data, human_stdout=_journey_human(data))


def main(argv=None) -> int:
    """Standalone wrapper; the root CLI normally renders ``dispatch``."""
    result = dispatch(argv)
    if result.human_stdout:
        sys.stdout.write(result.human_stdout)
    if result.human_stderr:
        sys.stderr.write(result.human_stderr)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
