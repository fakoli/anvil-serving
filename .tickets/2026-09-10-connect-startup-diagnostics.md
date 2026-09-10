# Bounded Connect startup diagnostics

Status: open; the startup-order defect is being fixed separately.

A coordinated gateway and connector upgrade failed when the connector requested
its challenge before the gateway ingress socket existed. The manager restored
the previous services. Its failure envelope identified only a partial operation;
the connector logs command returned byte counts without the causal message, and
the aggregate gateway logs command failed. A bounded native journal read was
needed to identify the missing-socket response and connector exit.

Provide fixed, bounded startup diagnostics through the managed surface. Preserve
component identity, timing, exit state, and an allowlisted failure category;
never expose credentials, capability-bearing URLs, cookies, request headers, or
arbitrary upstream bodies. Keep real deployment evidence in private operator
storage. Prove the behavior through the actual standalone manager entrypoint,
including partial component-log failure and output bounds.
