# Request-to-evidence coverage

| Requested outcome | Retained evidence | Result and boundary |
|---|---|---|
| Expand C1 context under memory reserve | preflight160-final.json; quality160-final.json; vision160-final.json; capacity160-short.json; capacity160-long.json; depths160-final.json | 160K qualified; measured maximum input 150,144 tokens |
| Retain output allowance | preflight160-final.json; depths160-final.json | 8,192-token requested allowance, not observed generated length |
| Preserve failed resource trials | feasibility-196608-result.json; feasibility-204800-result.json; friction-log.md | Host-cache pressure and policy-infeasible envelopes retained |
| Native client compatibility | client-compatibility.json | Bounded tool probes; initial macOS shell-marker failure remains unexplained |
| Operational deployment and restoration | private operator records | Actual assignments, topology, fleet state and dashboard receipts are not public benchmark data |

No retained artifact exceeds 400 KiB; every retained public artifact is under 1 MiB.
