# Synchronize the member-aware MCP catalog compatibility contract

Status: source candidate locally integrated; consolidated acceptance pending.

The integrated regression suite at f04cbfb9 passed 6,352 tests with ten skips
and failed only the pinned public MCP catalog snapshot in
tests/test_mcp_foundations.py. Scheduler T009 deliberately added the optional
member argument to router_transition, but its task scope omitted this pinned
catalog contract. The handler catalog digest remained unchanged.

Add a bounded T009.1 follow-up: characterize the exact member schema and
unchanged required arguments, verify the catalog delta, and update the public
catalog digest. Do not blindly bless unrelated catalog changes. Run the MCP
foundation, router transition, and command contract tests, then include the
candidate in the final integrated suite and consolidated acceptance pass.

This is a hermetic compatibility-test correction, not deployment evidence.

Candidate 393bd343 passed 128 focused tests and Ruff after commit, recorded as
EVA56E5250. Reverting only the member property, derived property-count ceiling
and declared-member description in a deep copy reconstructs the exact previous
catalog digest; the handler digest remains unchanged.
