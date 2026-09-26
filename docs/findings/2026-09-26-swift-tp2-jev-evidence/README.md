# Swift TP2 Jev evidence

This is a sanitized, bounded review bundle for the completed Swift TP2 and Jev
pilot campaign. It is a finalized evidence bundle: the candidate is not qualified or promoted, and the exact GLM incumbent was restored on the current host. Sanitized downstream comparison records are included for
reproducibility, while raw private events, provider sessions, and inherited
environment text remain excluded.

The retained inputs cover the pinned candidate identity and recipe, r1 and r2
configuration failures, r3 TP-rank placement and protocol smoke, and the
frozen 30-packet Jev shadow corpus. `redaction-ledger.json` records the
campaign-level substitutions. `pilot/public-redaction-bindings.json` binds the
30 frozen packets and their shadow receipts; `pilot/replay-codex-004/receipt-
redaction-bindings.json` separately binds the 90 downstream replay receipts.
`native/redaction-bindings.json` binds the original eight sanitized native artifacts; `native/swe-redaction-bindings.json` binds the retained SWE records.

No direct alias, serve, or gateway configuration is represented as changed by
this bundle. `promoted` remains false.

`native/swift-flash-tp2-r3.recipe.toml` is an exact historical recipe export,
including its generic in-container Hugging Face cache path and nominal 3 GiB
planning reserve. It is not a statement of the measured r3 state: the bounded
r3 trial used a 1 GiB reserve and observed 1,543 MiB free per GPU. See
`native/native-artifact-registry.json`; execution flags were not rewritten.

The original replay accounting is 150 provider calls, including five failed calls whose usage is unknown; it is not 150 plus five. With the four supplemental live-shadow requests, the retained instrumented total is 154 requests, 1,599,815 known tokens, and five calls with unknown failed usage. Development, corpus-authoring, and review-session usage are outside that total.

The retained SWE record is 2/5 on the fixed selected-task denominator: Sphinx and Sympy resolved, Django and Pylint did not, and xarray ended `LimitsExceeded` with an empty patch. The native wrapper remains failed and the official grader is incomplete; this does not qualify or promote the candidate.
