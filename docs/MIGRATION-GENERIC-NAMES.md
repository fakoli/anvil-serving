# Migrating to generic names

Earlier releases shipped names taken from the original author's lab. They are
now generic. **Old names keep working for one release** and emit a
`LegacyNameWarning` that names the replacement and links here. Nothing changes
on a running host until you choose to migrate.

## What changed

| Kind | Old name | New default | Where |
| --- | --- | --- | --- |
| Docker volume holding model weights | `fakoli-models` | `anvil-models` | multiplexer built-in registry, `docker-compose.heavy.yml`, scaffold `docker-compose.yml` |
| Compose project (experiments) | `fakoli-experiment` | `anvil-experiment` | `docker-compose.experiment.yml`, `docker-compose.q36.yml` |
| Compose project (flexibility) | `fakoli-flexibility` | `anvil-flexibility` | `docker-compose.flexibility.yml` |
| Voice forwarding bridge owner | `mini` | `companion` | `ForwardingBridgeService` only (`ProxyProcessConfig.owner` is a topology host id, not renamed) |

Behavior with old names:

- **Volume.** The multiplexer uses `ANVIL_MODELS_VOLUME` if set. Otherwise it
  uses `anvil-models`, unless only `fakoli-models` exists on the host, in which
  case it keeps using `fakoli-models` and warns. A registry JSON that names
  `fakoli-models` loads unchanged and warns.
- **Bridge owner.** `owner="mini"` is still accepted and warns. Any other
  value other than `companion` is rejected, as before.
- **Compose projects.** Compose files cannot warn. The new file default is the
  new name. The files set a fixed `container_name`, so running `up` under the
  new project name while the old project's containers exist fails with a
  container name conflict; it does not create a second project. Use `-p` as
  shown below to stay on the old project.

Unchanged by design: `fakoli` in GitHub URLs and the "Fakoli ·" Grafana Fleet
dashboard titles and uids.

## Check which names a host uses

```sh
docker volume ls --format '{{.Name}}' | grep -e fakoli-models -e anvil-models
docker compose ls | grep -e fakoli- -e anvil-
grep -rn -e fakoli-models -e '"owner"' <your registry JSON and operator config>
```

If only `fakoli-*` names appear, the host is on the old names.

## Migrate a live host

Do this in a maintenance window; each step is yours to run.

1. Stop the stack: `docker compose -p fakoli-experiment down` (and the same for
   `fakoli-flexibility`). Volumes are kept.
2. Weights volume: Docker cannot rename a volume. Create the new one and copy:

   First check free disk space against the volume size. The copy doubles disk
   use until step 7.

   ```sh
   docker run --rm -v fakoli-models:/from:ro alpine du -sh /from
   df -h /var/lib/docker
   docker volume create anvil-models
   docker run --rm -v fakoli-models:/from:ro -v anvil-models:/to alpine cp -a /from/. /to/
   ```

   Verify the contents, then keep `fakoli-models` until you are satisfied.
3. Upgrade anvil-serving and `unset ANVIL_MODELS_VOLUME` (or set it to
   `anvil-models`). Update any registry JSON `volume` fields and external
   compose files that reference `fakoli-models`.
4. Bring the stacks up again with the new compose files; the project names
   change to `anvil-experiment` / `anvil-flexibility`. Remove the old networks
   if they linger: `docker network rm fakoli-experiment_default` and
   `docker network rm fakoli-flexibility_default`.
5. Voice bridge: change `owner="mini"` to `owner="companion"` wherever you
   construct `ForwardingBridgeService`. (`ProxyProcessConfig.owner` is the
   topology host id and does not change.)
6. Confirm no warnings remain: run your usual commands with
   `python -W error::FutureWarning`.
7. When satisfied, `docker volume rm fakoli-models`.

## Stay on the old names temporarily

- Volume: `export ANVIL_MODELS_VOLUME=fakoli-models` (warns).
- Compose projects: pass `-p fakoli-experiment` (or `-p fakoli-flexibility`) per
  command, e.g. `docker compose -p fakoli-experiment -f docker-compose.experiment.yml up -d`.
  Do not export `COMPOSE_PROJECT_NAME`: it would rename every compose project in
  that shell, including primary-node production. Point services at the old
  volume with your own compose override.
- Bridge: keep `owner="mini"` (warns).

The old names stop being accepted in the next release.
