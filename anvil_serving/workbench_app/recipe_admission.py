"""Conservative recipe admission through a declared manifest owner.

Standalone recipe execution cannot reserve capacity. A portal load therefore
requires an exact recipe command in a manifest, full-card reservations and
explicit physical placement. Existing managed serve admission owns the start.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os

from .. import reservations, serves
from ..control_plane.mcp.errors import ToolError
from ..topology import load_topology


def load_plan(args, recipe, inventory):
    manifest, name, topology_path = (args.get(key) for key in ("manifest", "serve", "topology"))
    owner = (args.get("command_host"), args.get("command_runtime"))
    if not all(type(value) is str and value for value in (manifest, name, topology_path, *owner)):
        raise ToolError("recipe_owner_required", "Declare a managed manifest, serve and topology for this recipe before loading it.")
    scope = serves.load_manifest_set(manifest)
    matches = [item for item in scope if item["name"] == name]
    if len(matches) != 1:
        raise ToolError("recipe_owner_mismatch", "The selected recipe needs one exact manifest owner.")
    target = matches[0]
    expected_tail = ["models", "recipes", "load", recipe["model"], "--registry", os.path.abspath(args["registry"]), "--container", args["container"], "--confirm"]
    command = target.get("up") or []
    if (target.get("runtime") != "docker" or target.get("container") != args["container"]
            or target.get("model") != recipe["model"] or target.get("port") != recipe.get("serve", {}).get("port")
            or not command or os.path.basename(command[0]) != "anvil-serving" or command[1:] != expected_tail):
        raise ToolError("recipe_owner_mismatch", "The manifest must own this exact model, container, port and canonical recipe load command.")
    budgets = reservations.budgets_of(scope)
    declared = reservations.reservations_of(target)
    topology = load_topology(topology_path)
    roles = {role.id: role for role in topology.gpu_roles}
    # Recipes can contain engine-specific memory flags. Reserve each complete
    # physical card until a separate bounded co-residency contract is declared.
    if not declared or any(row.gpu_role not in budgets or row.gpu_role not in roles
            or row.vram_mib != budgets[row.gpu_role].budget_mib
            or roles[row.gpu_role].vram_mib != budgets[row.gpu_role].vram_mib
            or (roles[row.gpu_role].reserve_mib or 0) != budgets[row.gpu_role].reserve_mib for row in declared):
        raise ToolError("recipe_reservation_required", "Declare full-card GPU reservations for the recipe's managed serve.")
    placement = {roles[row.gpu_role].uuid for row in declared}
    selected = recipe.get("hardware", {}).get("gpu_uuid", "").split(",")
    if (set(selected) != placement or {(roles[row.gpu_role].host, roles[row.gpu_role].runtime) for row in declared} != {owner}):
        raise ToolError("recipe_placement_mismatch", "The recipe's explicit GPU UUIDs must match its manifest reservation roles.")
    states = serves.docker_states([item["container"] for item in scope if item.get("runtime") == "docker"])
    if any(states.get(item["container"]) not in {"absent", "created", "exited", "dead", "running", "paused", "restarting"}
           for item in scope if item.get("runtime") == "docker"):
        raise ToolError("recipe_owner_unavailable", "Cannot prove every declared reservation owner's current state.")
    def state_of(container):
        return states.get(container, "unknown")
    denial = reservations.deny_exclusive_conflict(scope, [target], state_of) or reservations.deny_over_budget(scope, [target], state_of)
    if denial:
        raise ToolError("recipe_capacity_denied", "The current managed GPU reservations or operating mode block this recipe. Review Compute before changing deployment.")
    tracked = {item["container"] for item in scope if item.get("runtime") == "docker"}
    if any(item.get("container") not in tracked and item.get("state") not in {"absent", "created", "exited", "dead"} for item in inventory.get("containers", [])):
        raise ToolError("recipe_capacity_denied", "A recipe outside this manifest still owns compute; reconcile that owner before loading another recipe.")
    fingerprint = hashlib.sha256(json.dumps({"scope": scope, "states": states, "owner": owner, "roles": [dataclasses.asdict(role) for role in topology.gpu_roles]},
        sort_keys=True, separators=(",", ":"), default=dataclasses.asdict).encode()).hexdigest()
    return {"admission_sha256": fingerprint, "manifest": manifest, "serve": name,
            "gpu_roles": sorted(row.gpu_role for row in declared), "reservation": "full-card"}
