# vLLM source builds from Windows can send CRLF shell helpers to BuildKit

- Status: fixed and image build accepted; managed model startup pending
- Observed: 2026-08-01
- Scope: `fakoli/anvil-vllm` source builds launched from a Windows worktree

## Symptom

The pinned SM120 vLLM image build failed in the `extensions-build` stage with:

```text
/usr/bin/env: 'bash\r': No such file or directory
```

The first failing file was `tools/ep_kernels/install_python_libraries.sh`. All
132 tracked `*.sh` files in the Windows worktree contained CRLF line endings
even though the committed blobs used LF. The repository did not define an EOL
attribute for shell scripts.

After that class was fixed, the build advanced and exposed a second shell-read
file: `build_rust.sh` extracted the toolchain channel from
`rust-toolchain.toml` as `1.95\r`, which rustup rejected as an invalid toolchain
name. This is the same root cause at a metadata boundary rather than a shebang.

The third build completed the expensive native-extension stage, then failed at
wheel packaging because BuildKit mounted the Windows worktree's `.git` pointer
file. Its `C:/Users/...` target is valid to Git on the Windows host but cannot be
resolved by `setuptools-scm` inside the Linux build container:

```text
LookupError: setuptools-scm was unable to detect version for /workspace.
```

## Root cause

The Windows checkout converted tracked shell scripts before Docker assembled
the Linux build context. The container received the worktree bytes, so the
shebang became `#!/usr/bin/env bash\r` and failed before dependency compilation.

## Durable fix

Add `*.sh text eol=lf` and `rust-toolchain.toml text eol=lf` to the engine
repository's `.gitattributes`, normalize the worktree, and retain the
attributes in the companion engine change. Do not patch a running container or
rewrite only the first file that happened to fail.

Keep the wheel step's read-only `.git` bind mount for normal full checkouts, but
also accept an explicit `VLLM_VERSION_OVERRIDE` build argument. Windows
worktree builds calculate that value from the pinned host revision and pass it
to `setup.py`; model weights remain in the named `vllm-hfcache` data volume and
compiler state remains in persistent BuildKit/ccache mounts.

The companion commit gate also exposed two pre-existing Windows test-runner
assumptions: Docker metadata tests used `os.uname()` instead of portable
`platform.machine()`, and the versions validator requires a UTF-8 console when
printing its success marker. The test uses the portable API; Windows validation
runs set `PYTHONUTF8=1` rather than weakening or skipping the check.

## Acceptance

- A fresh Windows checkout reports `eol: lf` for tracked `*.sh` files.
- The exact pinned Docker build passes the former `extensions-build` step.
- A Windows worktree build can package the wheel with a deterministic
  `VLLM_VERSION_OVERRIDE` while retaining the read-only Git mount.
- The resulting image records the patched engine commit and starts the managed
  DeepSeek V4 NVFP4 DSpark recipe.

## Build acceptance evidence

- Engine commit: `52113932444ed3b8f2228b2589ef2ff3cedf7ab2`
- Source version: `20260803.dev4+g521139324.d20260801`
- BuildKit result: completed 91/91 steps in 4,227.4 seconds
- Local immutable image:
  `anvil-vllm@sha256:76de0c41d9b216e17e81cfe89d9989c14e693c36eb76efaa860f8b27ef35d806`
- Image size: 8,190,971,391 bytes
- Identity probe: PyTorch `2.13.0+cu130`, CUDA `13.0`, FlashInfer
  `0.6.15.post1`, and the main-layer NVFP4 / draft-layer MXFP4 routing
  assertions passed for layers 42, 43, and 45.
