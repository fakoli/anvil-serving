"""Anvil Agent: talk to your own agents from smart glasses, and let them drive the HUD (ADR-0049).

A product family of its own. It consumes Anvil Voice speech-to-text through the
Capability Gateway with a route-bound device key, and it relays turns to agent
backends the operator declares (a Hermes API server, or Pi through pi-web's
mobile bridge), holding their credentials so the glasses client never does.
"""
