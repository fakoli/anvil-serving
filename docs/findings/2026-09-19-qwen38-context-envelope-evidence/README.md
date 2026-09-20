# Context envelope evidence

This sanitized bundle retains 128K direct-I/O diagnostics and final 160K/C1 qualification. Depth retrieval passed 9/9 at 150,058, 150,144 and 150,124 prompt tokens. Each case has three attempts; first requests were cold or partly cached and later requests warm. The 8,192-token output allowance is a request cap, not an observed generated length.

- [Preflight](preflight160-final.json), [quality](quality160-final.json), [vision](vision160-final.json)
- [Depth retrieval](depths160-final.json), [short capacity](capacity160-short.json), [long capacity](capacity160-long.json)
- [Startup telemetry](load160-final.telemetry.json), [client compatibility](client-compatibility.json)
- [Canonical manifest](artifact-manifest.json)

Failed host-cache resource trials and policy-infeasible 192K/204800 estimates remain retained. No matched speedup or endurance claim follows. Public artifacts use LF and redact operator identities and endpoints; hashes bind the sanitized bytes. Actual deployment assignments, authorization, restoration, topology and fleet/dashboard receipts remain private.
