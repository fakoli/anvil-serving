"""Protocol-independent foundations for the MCP compatibility facade.

The public API remains :mod:`anvil_serving.mcp`.  Modules in this package do
not import that facade; callers provide the catalog and dispatch functions
explicitly so composition stays acyclic.
"""
