# Synchronize the controller diagnostic workbench catalogs

Status: open; controller-diagnostics:T010 prerequisite to final source gates.

The generated-manifest gate exposed a separate real drift: the runtime MCP
catalog contains controller_inspect and controller_logs but both discoverable
workbench skills omit them. The existing test
`test_repo_workbench_surfaces_catalog_current_mcp_tools_and_cli_gaps` fails
after 21 preceding command-tree checks pass. Preserve the test and add the two
tools plus concise metadata-only, local-unreachable and evidence-limit guidance.
The two skills already differ in unrelated supporting content; synchronize this
addition without overwriting either. No runtime or live change is required.
