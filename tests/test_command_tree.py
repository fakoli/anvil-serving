
import pytest

from anvil_serving import mcp


def test_workload_tool_declarations_share_closed_query_schema_and_no_static_authority():
    from anvil_serving.observability.workload_tools import (
        fleet_workloads_declaration, node_workloads_declaration, parse_node_workload_query,
    )
    from anvil_serving.observability.workloads import (
        AGGREGATE_LIMIT, DEFAULT_QUERY_LIMIT, DEFAULT_RECENT_SECONDS,
        WorkloadKind, WorkloadOwner, WorkloadState,
    )

    node, fleet = node_workloads_declaration(), fleet_workloads_declaration()
    assert node["inputSchema"] == fleet["inputSchema"]
    for declaration in (node, fleet):
        assert declaration["_meta"] == {"anvil/requiredScope": "workloads:read"}
        assert declaration["name"] not in mcp.TOOLS
        schema = declaration["inputSchema"]
        assert schema["required"] == [] and schema["maxProperties"] == 7
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {
            "owner", "kind", "state", "host", "active_only", "recent_seconds", "limit",
        }
    properties = node["inputSchema"]["properties"]
    for key, enum in (("owner", WorkloadOwner), ("kind", WorkloadKind), ("state", WorkloadState)):
        assert properties[key] == {"type": "string", "enum": [item.value for item in enum]}
    assert properties["limit"] == {"type": "integer", "minimum": 1, "maximum": AGGREGATE_LIMIT, "default": DEFAULT_QUERY_LIMIT}
    assert properties["recent_seconds"] == {"type": "integer", "minimum": 1, "maximum": 86400, "default": DEFAULT_RECENT_SECONDS}
    assert properties["active_only"] == {"type": "boolean", "default": False}
    for invalid in ({"callback": "private"}, {"prompt": "private"}, {"limit": 1001},
                    {"recent_seconds": 86401}, {"active_only": "true"}):
        with pytest.raises(ValueError):
            parse_node_workload_query(invalid)
