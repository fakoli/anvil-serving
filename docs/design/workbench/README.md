# Workbench design study · revision 3

Open [index.html](index.html) directly in a browser. It works without a build,
network connection, credentials, or production services. The fonts are bundled
with their SIL Open Font License files and came from the Google Fonts repository
(`ofl/barlow` and `ofl/barlowcondensed`).

Read [DESIGN.md](DESIGN.md) for the workflows, architecture proposal, Open WebUI
fork comparison, Pi runner design, backend gaps and implementation slices.
Read [PI-REUSE.md](PI-REUSE.md) for the recommendation to test Pi Web reuse
before building a custom session UI or forking Open WebUI.
Use [CHECKPOINT.md](CHECKPOINT.md) to resume development, and
[VALIDATION.md](VALIDATION.md) for the actual verification scope.

Try these interactions:

1. In Workbench, create an experiment and simulate submission. Use **Run flow**
   to advance or pause deterministic phases; watch the runner indicator.
2. In Playground, change model, connector, preset and temperature. Send a demo
   request. An offline selection is rejected without substituting another model.
3. In Workbench, switch between Compare, Evidence, Events and All runs.
4. In Anvil work, open T-04 → Agent session. Pi is a chat with task folders and
   separate conversation threads. Create a conversation, branch, send a simulated
   turn, steer, finish or stop. Its operator model is independent of the benchmark.
5. Edit the Finch recipe, save a candidate and review its simulated MacBook load.
   Inspect the exact active revision in Playground. Saving alone does not load.
6. Open Settings → Access & sessions. Edit Researcher grants or revoke the browser
   session; its linked terminal session is revoked too. The last admin is protected.
   General preferences and Pi environment defaults have separate Save/Discard.
7. Open Observability and switch between the seven dashboard groups. Change the
   location and sample window, inspect chart values, filter logs, and try stale
   telemetry. Missing sources remain visible; benchmark windows remain historical.
8. Open Compute, select a host and workload, and inspect its container/process name.
   Filter Logs, review Configuration, or enter an explicitly non-executing Exec
   command. Start/Stop reviews update the same demo deployments as Playground.
9. Open Documentation to read Anvil/Serving summaries and their source links.
   Ctrl/Cmd+K finds pages and actions across the portal.

Everything is simulated. Playground responses are fixed canned text. Reloading
resets demo state. The prototype does not start containers, call models, change
Anvil State, change Connect grants, connect to Grafana, or operate serves. Pi Web
is a reuse candidate, not an installed dependency. The recipe editor covers a
small field projection, and the documentation reader contains curated summaries.
Connection and preset persistence is not built.

The original application files are untouched. The standalone study is outside
the packaged dashboard and must not be deployed as a replacement dashboard.
