# Device-key catalog reconciliation

An enrolled inference device key could read `/v1/models` but was denied both
metadata endpoints used by `clients sync`. Native propagation therefore failed
before catalog comparison, despite healthy inference and valid account access.

Derive client catalog reads from the existing discovery grant, filter every
alias to the key, and expose only client-required fields. Preserve the global
opaque config digest and all account, expiry, revocation, rate and audit checks.
Do not distribute a privileged router token to repair client synchronization.

Source validation covers real HTTP catalog fetch, sibling filtering, denied
queries and privileged endpoints, malformed limits, revocation, expiry and
account disablement. Deployment acceptance remains pending until the managed
router update and native fleet checks complete.
