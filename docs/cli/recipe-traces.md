# Recipe trace artifacts

Inspect the diagnostic volume owned by one local recipe:

```sh
anvil-serving models recipes trace-artifacts MODEL --container NAME --registry recipe.toml
```

This Linux-local command inventories the exact named volume mounted at
`/diagnostics`. It does not start, stop, reconfigure, promote, or route a
model. Add `--output trace-export` after the candidate exits to copy regular
`.nsys-rep`, `.sqlite`, and `.qdstrm` files into a new directory with SHA-256
hashes. The source volume remains unchanged.

The command checks recipe ownership and container identity, rejects links and
non-regular entries, and limits a flat inventory to 256 files and 10 GiB.
Export refuses an existing output directory and detects files that change while
copying. After candidate removal, use `--retained-volume` instead of
`--container`; it requires no container attachments before or after inspection.
That retained-volume mode reports its narrower identity boundary explicitly.
