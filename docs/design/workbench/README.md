# Workbench design study

Open [index.html](index.html) directly in a browser. It works without a build,
network connection, credentials, or production services. The fonts are bundled
with their SIL Open Font License files and came from the Google Fonts repository
(`ofl/barlow` and `ofl/barlowcondensed`).

Read [DESIGN.md](DESIGN.md) for the workflows, architecture proposal, Open WebUI
fork comparison, Pi runner design, backend gaps and implementation slices.
Use [CHECKPOINT.md](CHECKPOINT.md) to resume development, and
[VALIDATION.md](VALIDATION.md) for the actual verification scope.

Try these interactions:

1. Create an experiment, edit its bounded parameters, review it, and simulate
   submission. Inspect the new sample record.
2. In Playground, change model, connector, preset and temperature. Send a demo
   request. An offline selection is rejected without substituting another model.
3. Open Experiments → Compare, then Evidence → Attach to Anvil task.
4. Open Anvil work → Pi sessions. Review the environment and inspect sample
   Activity, Changes and Tests.
5. Toggle stale telemetry. Current HUD values become unknown while retained
   historical run data stays visible.
6. Filter sample logs or use Ctrl/Cmd+K to find a page/action.

Everything is simulated. Playground responses are fixed canned text. Reloading
resets demo state. The prototype does not start containers, call models, change
Anvil State, connect to Grafana, or operate serves. Connection and preset
management dialogs describe proposed behavior; their persistence is not built.

The original application files are untouched. The standalone study is outside
the packaged dashboard and must not be deployed as a replacement dashboard.
