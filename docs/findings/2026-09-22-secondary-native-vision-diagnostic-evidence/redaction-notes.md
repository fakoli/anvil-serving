# Sanitization record

This draft replaces operator URLs and endpoint addresses, filesystem paths, hostnames, GPU UUIDs, container names and IDs, and operator-log content. The affected native artifacts remain present with their original schemas and outcome fields; redaction does not convert failed evidence into a successful result.

Original source paths, SHA-256 values, and byte counts are retained only in the private original-hash manifest outside this draft. The copied corpus and PNG assets are frozen synthetic workload material.

Final publication review also replaced the diagnostic corpus.path workspace prefix with its repository-relative synthetic corpus path. Native outcome fields and the private originals remain unchanged.
