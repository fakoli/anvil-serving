# Prove router membership before single-serve lifecycle shortcuts

Status: open; qualified-replica-sets:T012 and children.

Independent source preflight found that _mode_router_plan returns None for an
unrouted exclusive target while cmd_mode can still drain/restore routed victims.
The manifest's router_tier string cannot prove direct versus replica membership.
Profile noop bypasses the mode planner as well. A later config-install refusal
therefore cannot establish the promised zero-mutation boundary.

The approved design requires a parsed active config for every routed affected
tier, including stopped potential victims and restore members; the CLI owns
explicit/default loading without network discovery. Missing metadata fails
closed as an intentional compatibility change. Promotion and up-for use their
existing explicit configs. Separate the shared promotion guard, mode/profile
ownership and explicit legacy-fixture migration into independently reviewed
children, then run the unchanged complete lifecycle assertions at integration.
