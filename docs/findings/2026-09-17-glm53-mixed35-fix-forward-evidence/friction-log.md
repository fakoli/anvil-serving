# Friction log

- **Disabled-thinking control:** The 256-token control exposed reasoning text and
  reached length before valid JSON. It remains a failed diagnostic; the
  default-thinking/max-16384 control passed and is retained in `preflight.json`.
- **Host-memory loading:** The preceding unbounded load exhausted host memory.
  The R7 streaming patch and managed 60 GiB/no-additional-swap containment
  recovered C1 and C4 operation. Later bounded quality and long-needle checks
  passed; broader repository, session/client, and full-context gates remain
  unqualified. Both trials restored the baseline.
- **Baseline strict C4 capacity:** Two of four responses contained 63 instead
  of the required 64 code words. Canaries and owner health passed, but strict
  output validation failed; retain the cell and withhold throughput.

- **Candidate strict C4 repeat:** The first cell passed 4/4. The second passed
  3/4; one answer contained 65 instead of 64 code words. Its throughput is
  withheld. This is a format failure, not an engine crash or coding verdict.
