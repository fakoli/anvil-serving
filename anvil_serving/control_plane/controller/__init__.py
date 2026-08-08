"""Internal implementation for :mod:`anvil_serving.controller`.

The compatibility facade points inward to this package.  The dependency
direction is::

    facade -> cli, server, http, catalog, store, security
    cli -> server, catalog, http, security
    server -> http, security, store
    http -> catalog, security, store
    catalog -> public anvil_serving.mcp
    store -> security
    security -> errors, shared anvil_serving.envfile (durable token fallback)

Internal controller modules must not import the compatibility facade or MCP
internals.
"""
