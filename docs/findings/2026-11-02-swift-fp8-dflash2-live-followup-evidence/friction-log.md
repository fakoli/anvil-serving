# Friction log

| Stage | Failure | Disposition |
|---|---|---|
| Functional preflight | Responses API received HTTP 403 from existing credential scope. | Preserved; no authorization change or retry. |
| Capacity | Six routed 64K/C1 streams failed the opening-canary contract. | Preserved; timing excluded and remaining capacity cells skipped. |
| Context | 224K nominal request received HTTP 413 at router admission before upstream. | Preserved; one separately labelled smaller probe ran under the addendum. |
