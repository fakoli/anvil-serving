# Planning-capability evidence publication record

- Public copy date: 2026-07-22
- Canonical public source revision: `fakoli/anvil-serving@31d95adaf68157b81318325356516cef9569b10f`
- Bundle tree at that revision: `6e26ebc80b853e151075e8807c0bba084480f823`
- Notes mirror revision inspected: `fakoli/anvil-serving-notes@7b46ceb6ae62252f8f808f6c065706a24e7970bb`
- Notes import revision: `71b0124f541840f3276e118db00aeb4a4126f99a`
- Canonical public-source bundle before publication edits: 21 files, 159,272 bytes
- Admission decision: publish the complete bounded bundle; every file is below 1 MiB and the bundle
  is below ADR-0027's 5 MiB aggregate limit
- Sanitization scan: no user-home paths, email addresses, API keys, authorization values, session
  identifiers, or secret-like values matched
- Publication normalization and edits: restore the imported checkout to canonical LF Git bytes;
  make local generation transactional and fail closed; bind aggregation to the six independently
  judged output hashes; add Ruff file directives to the historical offline calculators; and remove
  trailing spaces from four Markdown model outputs so repository whitespace gates pass.
  Non-whitespace output text, prompt text, judge records, metrics, and manifests are otherwise
  unchanged; the source hashes preserve the exact originals
- 2026-07-22 verification: `python grade_struct.py` followed by `python aggregate.py` reproduced the
  tracked structural scoreboard, judge aggregate, and CSV/JSON outputs with a clean Git diff;
  missing confirmation, partial request failure, and changed-output aggregation probes fail closed
- Not rerun: local/frontier generation and blind judging. Model IDs, endpoints, timings, and quality
  scores remain historical observations from 2026-06-28.

`SOURCE-SHA256SUMS.txt` records the canonical Git blob bytes for every source artifact at the public
source revision. The three edited scripts and
four whitespace-normalized output files will not match those source hashes; those differences are
intentional and fully enumerated above.
