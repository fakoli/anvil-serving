# Pinned candidate reconstruction

This is an experimental recipe, not a promoted configuration. The image is a
local immutable build, not a published image. Build `Dockerfile.preparation`
against NVIDIA/flare-local-inference commit
`47581ccac82660953a5b59cb04b2750d64edc97d` through the managed image build
surface. The exact resulting image ID is retained in `serve-recipe.toml`;
a future rebuild must record its own identity rather than assume byte equality.

Install the reviewed `seccomp.json` at the operator-selected absolute path and
update only that path in the recipe. Its hash is pinned. The profile adds only
io_uring_setup, io_uring_enter and io_uring_register to the pinned Moby default
at 4b8214fc4971e06172938a1f1554ccff6647ee80. Keep it immutable through launch.
The published path is synthetic. Stage `flare-config.toml` in the private
configuration volume. The explicit recipe `--compile-workers 8` overrides
its configuration-file value of 2.

Use managed `models recipes load`, `status`, `logs`, and `unload` with this
registry. The tested source adds pinned inspection-image and seccomp support;
these changes are not yet released. GPU ownership and host admission must pass.
The private operator packet retains exact selected GPU identities and commands.

Named model, kernel/package and private configuration volumes survive managed
unload. The private volume contains a local package-signing key; never publish
or copy that key into evidence. Completed compiler caches are reusable with
this pinned runtime/hardware. Package export succeeded. The retained package index hash is in
[package identity](../raw/candidate-package-identity.json); it excludes external
weight blobs. A cached native restart took21.555seconds and passed7/7 preflight.
32,768 is the tested workload budget, not a Flare runtime context limit. TP2/C4/MTP3 and 2 GiB reserve are configured values.
No supported vision replacement or MTP speedup is established by this recipe.
