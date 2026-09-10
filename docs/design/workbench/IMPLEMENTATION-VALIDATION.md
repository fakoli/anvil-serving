# Workbench implementation validation map

This map connects the approved stories in
[IMPLEMENTATION.md](IMPLEMENTATION.md) to the shipped user journey, its
server-owned implementation, and focused regression evidence. It is a design
and test map. It does not declare a release, merge, installation, browser smoke,
or live serving validation complete.

| Story | Workspace and visible journey | Real backend boundary | Focused evidence |
| --- | --- | --- | --- |
| US-SHELL-01 | Use the shell navigation, command palette, page refresh and saved layout across Workbench, Playground, Models, Anvil work, Observability, Compute, Documentation and Settings. | `observatory.js`, authenticated dashboard session and Workbench catalog. | `tests/workbench/test_frontend_contracts.py`, `tests/workbench/test_service.py` |
| US-BENCH-01 | Open Workbench, choose a declared experiment, inspect target/limits/phases, start only the owner operation, then inspect retained evidence and comparison. | Existing controller experiment projections and `static/views/workbench.js`/`experiments.js`; no model self-judging. | `tests/workbench/test_frontend_contracts.py`, existing runtime-experiment tests |
| US-PLAY-01 | Choose a configured connector, exact model and preset; send, stream, cancel, reopen or delete a conversation. | `workbench_app.playground.Playground`, `PrivateStore`, protected credential resolver and `static/views/playground.js`. | `tests/workbench/test_playground.py`, `tests/workbench/test_credentials.py` |
| US-RECIPE-01 | Browse a declared recipe, inspect/edit supported canonical fields, review its revision, then invoke the existing owner lifecycle. | Existing configuration/controller owner APIs and `static/views/models.js`/`configuration.js`. | `tests/workbench/test_frontend_contracts.py`, existing controller configuration tests |
| US-WORK-01 | Select a configured project, open PRDs and a task packet, create task work, inspect patch/evidence preview, verify, submit or release. | `workbench_app.projects.Projects`, `TaskArtifacts`, `EvidenceJobs`; all State reads/claims/evidence use the Anvil CLI. | `tests/workbench/test_projects.py`, `tests/workbench/test_task_artifacts.py`, `tests/workbench/test_evidence_jobs.py`, `tests/workbench/test_task_sandbox.py` |
| US-PI-01 | In the task's thread folder, start/select/resume/branch a Pi thread, choose permitted model/thinking, prompt, steer, stop, inspect tool events and answer supported extension prompts. | `PiConversationService`, `PiSessionStore`, `PiRpcClient`, official Pi RPC, and `static/views/pi_chat.js`. | `tests/workbench/test_pi_rpc.py`, `tests/workbench/test_pi_sessions.py`, `tests/workbench/test_pi_official_smoke.py` |
| US-PI-02 | Reopen a task after browser/process interruption and recover cursor/session state without replaying a command or crossing a lease boundary. | Durable Pi session/intent records, server runner inspector, task-binding revalidation and isolated runner policy. | `tests/workbench/test_pi_sessions.py`, `tests/workbench/test_pi_runner.py`, `tests/workbench/test_pi_storage.py`, `tests/workbench/test_pi_egress.py` |
| US-OBS-01 | Switch source-labelled Fleet, model, GPU, host, benchmark, log and monitoring panels; refresh without a model call. | Existing observability projections and `static/views/observability.js`. | `tests/workbench/test_frontend_contracts.py`, existing observability logs/adapter tests |
| US-COMPUTE-01 | Select a configured host/workload, inspect identity/configuration and read bounded retained logs before reviewing owner controls. | Controller adapter workload projection and `static/views/compute.js`. | `tests/workbench/test_workload_logs.py`, `tests/observability/test_controller_adapter.py` |
| US-EXEC-01 | Choose an owner-declared diagnostic, review its immutable container, owner and execution limits, then inspect bounded output. Private arguments remain at the owner, pinned by the reviewed digests. | `workbench_app.container_exec.ContainerExec` and controller service capability policy. | `tests/workbench/test_container_exec.py`, `tests/observability/test_container_exec_http.py` |
| US-SETTINGS-01 | Save bounded workspace preferences, inspect configured connections and storage ownership, use existing Connect access controls, and read fixed packaged docs. | Workbench preferences/catalog endpoints, existing Connect adapter, fixed document catalog and `static/views/settings.js`/`documentation.js`. | `tests/workbench/test_documentation.py`, `tests/workbench/test_service.py`, `tests/observability/test_observatory_connect_access_ui.py` |
| US-DELIVERY-01 | Build the pinned Pi runner, review bounded storage setup, launch the authenticated dashboard, and consult packaged guidance. | `anvil-serving workbench build --runner pi`, `pi-storage`, dashboard asset package and fixed docs catalog. | `tests/workbench/test_packaging.py`, `tests/workbench/test_pi_storage.py`, `tests/workbench/test_javascript_modules.py` |

## Task-to-evidence path

The task evidence buttons do not turn an agent response into acceptance. A task
start first obtains a lease and freezes the task/PRD packet. Pi edits its
isolated full clone. Review stops the runner, captures a baseline-relative patch
in a networkless pinned sandbox, rejects metadata/symlink/submodule paths and
unexpected modes, then validates the patch digest and declared scope. It runs
the frozen verification commands in a disposable verifier, retains bounded
stdout/stderr, exit code, command identity and timing, and transfers only a
passing patch to a pristine claimed worktree. The submit button records actual
evidence through Anvil State. State acceptance remains independent.

## Pi operating boundary

Pi is not an imported Pi Web service. The browser uses the task-folder chat and
the server talks to the official RPC agent. Each session receives a fresh
isolated full checkout and native session/agent directory from the fixed pool.
The server journal and evidence are outside the pool. For a cloud provider, a
per-session reverse gateway has the exact declared provider endpoint and holds
the protected credential file; the agent receives neither the host credential
directory nor a secret-bearing browser request.

## Intended checks

Run focused checks before broad integration:

```bash
python scripts/run_tests.py tests/workbench/test_projects.py tests/workbench/test_task_artifacts.py tests/workbench/test_task_sandbox.py tests/workbench/test_pi_sessions.py tests/workbench/test_pi_storage.py -q
python scripts/run_tests.py tests/workbench/test_frontend_contracts.py tests/workbench/test_packaging.py tests/workbench/test_documentation.py -q
```

Use the full suite and a rendered authenticated browser journey only as later
delivery evidence. A fixture, dry-run, or unit test does not replace those
checks.
