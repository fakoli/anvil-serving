# Closed runtime evidence for reasoning probes

The recipe status projection deliberately omits command arguments, environment,
mounts and runtime file contents. It cannot establish the package/template byte
parity or tokenizer markers required for the isolated reasoning probe.

Resolved by the narrowly scoped `eval reasoning-limit-inspect` command. It binds
the managed recipe and immutable container identity, executes a fixed CPU-only
file/tokenizer inspection, and rechecks identity. It exposes only selected
non-secret reasoning controls and artifact hashes. It never opens inference,
downloads artifacts, imports model code, or accepts arbitrary executable input.
