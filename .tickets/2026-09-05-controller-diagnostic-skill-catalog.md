# Synchronize the controller diagnostic workbench catalogs

Status: source-resolved; controller-diagnostics:T010 accepted.

The generated-manifest gate exposed a separate real drift: the runtime MCP
catalog contains controller_inspect and controller_logs but both discoverable
workbench skills omit them. The existing test
`test_repo_workbench_surfaces_catalog_current_mcp_tools_and_cli_gaps` fails
after 21 preceding command-tree checks pass. Preserve the test and add the two
tools plus concise metadata-only, local-unreachable and evidence-limit guidance.
The two skills already differ in unrelated supporting content; synchronize this
addition without overwriting either. No runtime or live change is required.

Accepted source c9c8a3f440820dd388e828c7fcc21d0f57efda53 passed the existing
catalog audit, both skill validators and independent review. Removing one new
entry made the audit fail. State proof: controller-diagnostics-T010-E000677.
Final repository and deployment gates remain separate.
