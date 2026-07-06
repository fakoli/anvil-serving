# OpenClaw workbench skill smoke (2026-07-06)

This note records the live Fakoli Mini smoke check for the manual
`anvil-serving-workbench` skill install path. It is evidence for
`docs/OPERATOR-SKILLS-AND-SUBAGENTS.md`, not a replacement for the future
`anvil-serving harness sync openclaw --skills` renderer.

## Environment

- Host: Fakoli Mini (`ssh fakoli-mini`)
- OpenClaw: `2026.6.11 (e085fa1)`
- Shell prerequisite for non-interactive SSH:

```bash
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH
```

OpenClaw emitted a legacy config-health migration warning during these commands.
The warning did not block plugin inspection or skill visibility checks.

## Commands

```bash
openclaw plugins inspect openclaw-anvil-intent-router --runtime --json
openclaw skills install /tmp/anvil-serving-workbench-skill --as anvil-serving-workbench
openclaw skills info anvil-serving-workbench --json
openclaw skills check --json
```

## Evidence

Plugin runtime inspection for `openclaw-anvil-intent-router` reported:

```json
{
  "status": "loaded",
  "activated": true,
  "hookCount": 1
}
```

Skill inspection reported:

```json
{
  "name": "anvil-serving-workbench",
  "source": "openclaw-workspace",
  "filePath": "/Users/sdoumbouya/.openclaw/workspace/skills/anvil-serving-workbench/SKILL.md",
  "baseDir": "/Users/sdoumbouya/.openclaw/workspace/skills/anvil-serving-workbench",
  "eligible": true,
  "modelVisible": true,
  "userInvocable": true,
  "commandVisible": true
}
```

`openclaw skills check --json` included `anvil-serving-workbench` in the
`eligible`, `modelVisible`, and `commandVisible` skill lists. Its summary counts
after install were:

```json
{
  "eligible": 43,
  "modelVisible": 43,
  "commandVisible": 42
}
```

## Result

The checked-in OpenClaw workbench skill can be installed into the OpenClaw
workspace and made visible to models and commands. Provider/model config is a
separate prerequisite owned by `anvil-serving harness sync openclaw` or the
manual setup in `plugins/openclaw-anvil-intent-router/README.md`.
