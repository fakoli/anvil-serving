"""Command declarations for umbrella product and journey discovery."""

from .family import command_family
from .spec import CommandNode, _handler, _node


def _product_handler():
    return _handler("anvil_serving.product", attribute="dispatch")


@command_family(category="Start here")
def commands() -> CommandNode:
    return _node(
        "product",
        "Discover product families, boundaries, and ordered user journeys.",
        handler=_product_handler(),
        children=(
            _node(
                "families",
                "List the six product families and their boundaries.",
                handler=_product_handler(),
            ),
            _node(
                "journey",
                "Show the ordered journey for one product family.",
                handler=_product_handler(),
            ),
        ),
        docs_anchor="docs/cli/product.md",
    )
