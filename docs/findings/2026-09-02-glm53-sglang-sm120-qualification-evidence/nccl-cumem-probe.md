# Exact-image NCCL cuMem probe

Date: 2026-09-02

The probe used the candidate image by immutable digest, both local GPUs,
`--ipc host`, Torch distributed with two local NCCL ranks, one CUDA tensor per
rank, and a one-element all-reduce. It did not load model weights.

| Variant | Result | Decisive evidence |
|---|---|---|
| Image defaults | fail | NCCL 2.30.7 reported `ncclCuMemMapAndSetAccess` followed by CUDA error 999 on both ranks. |
| `NCCL_CUMEM_ENABLE=0` only | pass | NCCL selected `SHM/direct`; both ranks returned the reduced value `2.0` and exited zero. |
| cuMem off plus upstream `expandable_segments:True` | fail | Both ranks failed their first `torch.ones` allocation with CUDA error 999, before the collective. |
| cuMem off plus `expandable_segments:False` | pass | Both ranks allocated, NCCL selected `SHM/direct`, both returned `2.0`, and the process exited zero. |

The basic two-device CUDA allocation probe had already passed. Together these
results isolate the first managed startup failure to NCCL's cuMem allocation
path under this local WSL2 driver/runtime combination. The managed recipe
therefore adds `NCCL_CUMEM_ENABLE=0` and disables the expandable PyTorch CUDA
allocator. It does not copy unproven P2P, host-cuMem, or InfiniBand controls
from older recipes.
