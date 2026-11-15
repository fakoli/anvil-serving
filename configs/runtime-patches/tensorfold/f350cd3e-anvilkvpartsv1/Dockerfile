# The private build declaration supplies the reviewed author/toolchain image.
# This context contains only Anvil-owned changes, never the author's patches.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
WORKDIR /src/tensorfold
COPY . /opt/anvil-tensorfold-telemetry/
RUN export LC_ALL=C \
 && test "$(find /opt/tf-patches/engine -maxdepth 1 -name '*.patch' -type f | wc -l)" -eq 101 \
 && author_map=$( \
      printf '{'; separator=; \
      for patch in /opt/tf-patches/engine/*.patch; do \
        test -f "$patch" && test ! -L "$patch" || exit 1; \
        name=${patch##*/}; \
        case "$name" in *[!a-zA-Z0-9_.-]*) exit 1 ;; esac; \
        printf '%s"patches/engine/%s":"%s"' "$separator" "$name" "$(sha256sum "$patch" | cut -d' ' -f1)"; \
        separator=,; \
      done; \
      printf '}') \
 && test "$(printf '%s' "$author_map" | sha256sum | cut -d' ' -f1)" = 544dec546163a679ab4a62ccbd62c68a5a27608669b01d2739191ec09416378c \
 && printf '%s\n' \
      'c16ce7829dbe0cdac70ea433f067c4a48a575822a530a995462badeaf001eaff  /opt/anvil-tensorfold-telemetry/manifest.toml' \
      'eba39135b359c38a8be87d9c8599dd32bd6c524d27174f86ddf0d5a31dac69d4  /opt/anvil-tensorfold-telemetry/apply-anvil-patches.sh' \
      '8e838242dfb2608ef15fb7088fddcb9737fcf4ba7f29e0274315273fe4731cbe  /opt/anvil-tensorfold-telemetry/CONTRACT.md' \
      | sha256sum -c - \
 && sh /opt/anvil-tensorfold-telemetry/apply-anvil-patches.sh /src/tensorfold /opt/anvil-tensorfold-telemetry/manifest.toml \
 && git add -A \
 && test "$(git write-tree)" = 1c7fffb5975b3cc2e4a6c57b05b37f9c04e04cf6
RUN --mount=type=cache,id=tensorfold-zig-global,target=/root/.cache/zig \
    test "$(zig version)" = 0.17.0 \
 && zig test zig/src/core/lanes/anvil_telemetry.zig \
 && zig test -lc --dep engine_api -Mroot=zig/src/server/metrics.zig --dep lanes -Mengine_api=zig/src/core/engine_api.zig -Mlanes=zig/src/core/lanes/lanes.zig \
 && zig test -lc --dep lanes --dep sys -Mroot=zig/src/core/lane_host_anvil_test.zig --dep sys -Mlanes=zig/src/core/lanes/lanes.zig -Msys=zig/src/platform/sys.zig --test-filter Anvil \
 && zig test -lc --dep core --dep lanes -Mroot=zig/src/families/glm/cuda_kv_shard.zig --dep lanes --dep sys -Mcore=zig/src/core/root.zig --dep sys -Mlanes=zig/src/core/lanes/lanes.zig -Msys=zig/src/platform/sys.zig --test-filter Anvil \
 && zig build test-glm-kv-telemetry-host --summary all -j2 \
 && zig build native grammar --prefix /opt/tensorfold --summary failures -j2 \
      -Doptimize=fast -Dcpu=x86_64_v3 -Dstrip=false \
      -Dnvcc=/usr/local/cuda/bin/nvcc -Dsm=120 -Dxgrammar=/src/xgrammar \
 && /opt/tensorfold/native/bin/tensorfold-native --version
RUN cp /opt/tensorfold/BUILD-INFO /opt/tensorfold/BUILD-INFO.author \
 && { cat /opt/tensorfold/BUILD-INFO.author; \
      echo 'anvil_contract_version=1'; \
      echo 'anvil_source_tree=1c7fffb5975b3cc2e4a6c57b05b37f9c04e04cf6'; \
      sha256sum /opt/anvil-tensorfold-telemetry/manifest.toml; \
      sha256sum /opt/tensorfold/native/bin/tensorfold-native; \
      cat /opt/anvil-tensorfold-telemetry/manifest.toml; \
    } > /opt/tensorfold/BUILD-INFO
LABEL anvil.tensorfold.contract="1" \
      anvil.tensorfold.base="f350cd3ea253a042e205746928271f9d8f71a82d" \
      anvil.tensorfold.author-patches="101"
WORKDIR /workspace
