# Preserve terminal recipe startup status when cgroup data disappears

Date: 2026-10-26
Status: open

When a managed recipe container exits during startup, Docker may remove its memory cgroup before the readiness waiter finishes. The container identity then reports a terminal state such as `exited`, while its host-memory observation is incomplete. The waiter currently checks containment first and can return a generic containment error instead of the engine's terminal exit state, obscuring the startup failure.

The waiter should return terminal container states before evaluating live containment. For `created`, `restarting`, and `running` states, containment must remain fail-closed before any HTTP health probe. Keep the fix local to readiness; do not weaken shared containment checks or control-plane postconditions.

The preserved startup evidence reports a CSF loader registration failure; a publisher wrapper may bypass the intended bootstrap. Current managed recipe surfaces do not expose a read-only image `Entrypoint`/`Cmd` inspection. Follow-up diagnosis needs a narrow inspection of those two fields for the exact pinned image, without reading environment or secret fields. This ticket does not add that inspection surface.
