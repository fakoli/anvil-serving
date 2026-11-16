# Separate the native cache consumer descriptor budget

Status: fixed in source; 125 focused tests passed and independent review approved.

Whole-cache retirement refused a busy Linux desktop because its process and
file-descriptor scan shared the 20,000-entry metadata-tree budget. More than
20,000 unrelated open descriptors exhausted that budget before the complete
consumer audit could finish.

Count descriptors separately with a one-million-entry bound, matching the
independent mapping budget. Retain the 20-second deadline, complete process,
descriptor, mapping, cwd and executable checks, fail-closed errors, and all
other retirement gates. The regression checks detection of a late consumer
after exceeding the metadata budget, and refusal at the descriptor bound.
