# Explicit credential sources for managed serve launches

Status: open.

`serves._serve_env` unconditionally reads the shared home dotenv before operator and manifest dotenv files. A user who prohibits access to the shared file cannot use a managed mode launch even when every required credential is already provided by an explicit protected runtime source.

Provide an explicit bounded credential-source policy through the supported serve configuration, preserve compatibility deliberately, and test that opting out never opens or enumerates the shared file. Do not copy credentials into manifests, evidence or command arguments.

Current isolated qualification uses the existing recipe load/unload commands with explicit router quiesce/drain/readmit and exact baseline restoration. This avoids the shared-file reader but lacks the mode transaction's automatic compensation; per-step evidence and the preserved rollback remain required.
