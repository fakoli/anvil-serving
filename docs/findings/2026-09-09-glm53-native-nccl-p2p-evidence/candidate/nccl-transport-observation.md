# Sanitized candidate NCCL transport observation

This is a bounded public extract from the candidate startup record. The source
record's SHA-256 is
`91c221e6c2bd42be78a1c67c9e12184e3a45c0378e3632ed49deb4f97dec4d3f`.
Container, host, endpoint, process, PCI, and GPU-identity fields were removed.

- NCCL reported version `2.30.7+cuda13.3`.
- Its two TP channels reported `P2P/IPC` paths in both directions: `0 -> 1`
  and `1 -> 0`.
- Neither channel was reported as using an SHM path.

This startup observation proves the candidate runtime selected a P2P transport
path. It does not prove model performance, functional correctness, or a
promotion decision; those require the separately retained candidate gates.
