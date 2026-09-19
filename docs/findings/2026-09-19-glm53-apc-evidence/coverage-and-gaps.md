# Coverage and gaps

| Outcome | Evidence | Status |
|---|---|---|
| Pinned APC identity and containment | configuration-identity.json | Passed |
| Matched unique and shared C4 capacity | matched-comparison.json; 32/32 per arm and population | Both frozen gates passed |
| Actual shared-cache reuse | candidate-shared32k-cache-delta.json | 75.58% |
| Comparable diagnostic quality | baseline-quality-final.json and candidate-quality-final.json | 12/12 each, three repetitions, verified template provenance |
| Image behavior | Baseline and candidate eight-image artifacts; candidate OCR | Passed |
| Candidate long context | candidate-long-context.json | 309422-token needle; 110760-token tool call |
| Exact restoration | restoration.json and final direct/routed preflight | Verified |
| Promotion process | independent-review.md | Bounded promotion recommended; human approval pending |
| Broader intelligence, real SWE, video, full-context C4 | Not measured | Outside this campaign's evidence |

The failed 64-word baseline and negative eight-request unique scout remain
retained. Two-repetition quality diagnostics were superseded, not rewritten.
