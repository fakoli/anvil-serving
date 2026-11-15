# The private build declaration supplies the retained toolchain/codec image.
# This context contains only Anvil-owned changes, never the author's patches.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
WORKDIR /src
RUN mv tensorfold tensorfold-baseline \
 && git init -q tensorfold \
 && git -C tensorfold fetch -q --depth 1 https://github.com/ashhart/TensorFold.git f350cd3ea253a042e205746928271f9d8f71a82d \
 && git -C tensorfold checkout -q --detach FETCH_HEAD \
 && git init -q mia-recipe \
 && git -C mia-recipe fetch -q --depth 1 https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-RTX-PRO-6000-TensorFold.git fc1ee3a5381031e2357662b46c2fb44b20190429 \
 && git -C mia-recipe checkout -q --detach FETCH_HEAD \
 && test "$(git -C mia-recipe rev-parse HEAD)" = fc1ee3a5381031e2357662b46c2fb44b20190429 \
 && mv /opt/tf-patches/engine /opt/tf-patches/engine-baseline \
 && cp -a mia-recipe/patches/engine /opt/tf-patches/engine \
 && mkdir -p /opt/tensorfold/LICENSES/mia-recipe \
 && cp mia-recipe/LICENSE mia-recipe/NOTICE.md mia-recipe/CREDITS.md /opt/tensorfold/LICENSES/mia-recipe/ \
 && for patch in /opt/tf-patches/engine/*.patch; do \
      git -C tensorfold apply --check "$patch" && git -C tensorfold apply "$patch" || exit 1; \
    done
WORKDIR /src/tensorfold
COPY . /opt/anvil-tensorfold-telemetry/
RUN export LC_ALL=C \
 && test "$(find /opt/tf-patches/engine -maxdepth 1 -name '*.patch' -type f | wc -l)" -eq 110 \
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
 && test "$(printf '%s' "$author_map" | sha256sum | cut -d' ' -f1)" = 021bf7fc3b60c293287fe2a835fee511d1b7c20ec6c985cd31c37af117f79f09 \
 && printf '%s\n' \
      '701d98da5c904f7469a58013d9c3a0666a383ae24eaf88bfbb401b5f667004b7  /opt/anvil-tensorfold-telemetry/manifest.toml' \
      '0ac4f61b35db8dbb3c0b85f8b5074cd3e0d11004b609ce471954421dfed22956  /opt/anvil-tensorfold-telemetry/apply-anvil-patches.sh' \
      '8e838242dfb2608ef15fb7088fddcb9737fcf4ba7f29e0274315273fe4731cbe  /opt/anvil-tensorfold-telemetry/CONTRACT.md' \
      | sha256sum -c - \
 && sh /opt/anvil-tensorfold-telemetry/apply-anvil-patches.sh /src/tensorfold /opt/anvil-tensorfold-telemetry/manifest.toml \
 && git add -A \
 && test "$(git write-tree)" = 109491764fc5f711705c148da278a708f6c1aeea
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
      -Dgit-commit=f350cd3ea253a042e205746928271f9d8f71a82d \
      -Dpatches=109491764fc5f711705c148da278a708f6c1aeea \
 && /opt/tensorfold/native/bin/tensorfold-native --version
RUN cp /opt/tensorfold/BUILD-INFO /opt/tensorfold/BUILD-INFO.baseline \
 && { echo 'tf_ref=f350cd3ea253a042e205746928271f9d8f71a82d'; \
      echo 'recipe_commit=fc1ee3a5381031e2357662b46c2fb44b20190429'; \
      echo 'patches=110'; \
      echo 'zig=0.17.0 cpu=x86_64_v3 sm=120 optimize=fast strip=false'; \
      /opt/tensorfold/native/bin/tensorfold-native --version; \
      sha256sum /opt/tf-patches/engine/*.patch; \
      echo 'anvil_contract_version=1'; \
      echo 'anvil_source_tree=109491764fc5f711705c148da278a708f6c1aeea'; \
      sha256sum /opt/anvil-tensorfold-telemetry/manifest.toml; \
      sha256sum /opt/tensorfold/native/bin/tensorfold-native; \
      cat /opt/anvil-tensorfold-telemetry/manifest.toml; \
    } > /opt/tensorfold/BUILD-INFO
LABEL anvil.tensorfold.contract="1" \
      tf.patches="109491764fc5f711705c148da278a708f6c1aeea" \
      anvil.tensorfold.recipe="fc1ee3a5381031e2357662b46c2fb44b20190429" \
      anvil.tensorfold.base="f350cd3ea253a042e205746928271f9d8f71a82d" \
      anvil.tensorfold.author-patches="110"
WORKDIR /workspace
