# Product discovery commands

[Product families](../PRODUCT-FAMILIES.md) · [CLI overview](../CLI.md)

The read-only `product` family exposes the product map embedded in the
installed package. It reads no topology, contacts no endpoint, and changes no
operator state.

## List product families

```bash
anvil-serving product
anvil-serving product families
anvil-serving product families --json
```

The human view shows each family promise, boundary, root commands, and the
next journey command. JSON uses the standard CLI envelope; its `data` contains
the `anvil-serving.product-families/v1` catalog.

## Show an ordered journey

```bash
anvil-serving product journey model-serving
anvil-serving product journey capability-gateway
anvil-serving product journey evaluation-evidence
anvil-serving product journey anvil-voice
anvil-serving product journey anvil-media
anvil-serving product journey control-plane-fleet
```

Short aliases such as `serving`, `gateway`, `eval`, `voice`, `media`, and
`fleet` resolve to those stable ids. An unknown family is a typed usage error;
the command never guesses.

Each step names its stage, intent, CLI template, and expected outcome. Angle
brackets are placeholders that the operator must replace with declared local
values. Mutating steps still use the target command's normal preview and
confirmation policy.

## Automation contract

`anvil-serving --command-manifest` carries the same umbrella description and
six-family catalog. Every visible operational command record includes one
`product_family` id; only the read-only `product` discovery commands have a
null family. Command-tree validation fails when the operational root surface
and family partition diverge.
