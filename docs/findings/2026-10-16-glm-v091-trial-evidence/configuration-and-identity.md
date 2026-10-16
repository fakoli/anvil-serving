# Configuration and identity

R2 used `brandonmusic/GLM-5.3-Flash-tr3-4bpw@a5fee929cf4888b1824323e33e8a19b60129e025`,
v0.9.1 image `sha256:a9e985c09885710601b32ac86e81256aa1279b542caa345233ce331a816d7e07`,
and engine build source `84572a81e0092796d396acf4c3f22d6aabc3724b`. It used
uniform K4 EXL3/TR3 routed experts, native-precision non-routed tensors, FP8
DS-MLA KV, TP2/EP1/DCP1, 327680 configured tokens, C4, prefix caching,
chunked prefill, default/max reasoning, no speculation, and up to eight images
per request. The source checkout was `4c8b73ed0e09c70f3b4015a675cc243c4d86949a`.

The public reconstruction retains the managed controls: 0.94 GPU-memory
utilization, 2048 batched tokens, max capture size 4, B12X MLA sparse attention,
GLM45 reasoning and GLM47 tool parsers, host IPC, 32 GiB shared memory, 52 GiB
memory ceiling, 12 GiB host reserve, and disabled adaptive MTP, DFlash,
ReplaySSM, and startup warmup. Operational volume paths, device IDs, ports,
and mounts are redacted as generic managed resources.

The restored original is the pinned R11 DCP1/APC no-spec baseline at model
revision `a5fee929cf4888b1824323e33e8a19b60129e025` and image
`sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692`.
It retains its historical VRAM waiver; it is not reconstructed as a 4096 MiB
challenger-gate profile.

The restored baseline matched its captured container image, model, and model
revision. Container identifiers, operator paths, host names, GPU UUIDs, and
endpoint addresses were removed. The reconstruction is public-safe only; live
assignment and route state remain private.

Source identities: [weights](sanitization-provenance.json), [image](sanitization-provenance.json), and [restoration](restoration.json).
