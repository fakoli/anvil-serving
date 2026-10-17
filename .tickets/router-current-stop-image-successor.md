# Current-stop exact image successor

A completed controlled stop could bind a reviewed successor image, yet the
host bootstrap subsequently required the predecessor image ID and refused
startup. Allow only the independently pinned image reference to differ alongside
the existing config source aliases. Preserve every other Compose field, the
protected stop receipt, exact image/config digests, storage lineage and native
death/closure proof. Startup still establishes fresh closed custody before
separate acknowledged readmission.

Regression: the current-stop fixture now uses different exact old/new images;
image substitution, entrypoint/environment changes and other authority drift
must refuse before startup. No retained receipt or accounting is rewritten.
