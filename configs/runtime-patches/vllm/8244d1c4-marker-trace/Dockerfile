FROM ghcr.io/local-inference-lab/vllm:release-10152026@sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29

COPY 8244d1c4-split-topp/split-topp.patch 8244d1c4-split-topp/trace.patch 8244d1c4-split-topp/verify_patch.py 8244d1c4-split-topp/reference.py 8244d1c4-split-topp/native_comparator.py 8244d1c4-split-topp/kernel_regression.py 8244d1c4-split-topp/launch.py 8244d1c4-split-topp/README.md 8244d1c4-split-topp/UPSTREAM-LICENSE /opt/anvil-sampling/
RUN --network=none printf '%s\n' \
      'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4  /opt/anvil-sampling/UPSTREAM-LICENSE' \
      'c30ca1225f4a3b36b1e015738d9cf34129b1876851122d9da1abea1e5097fc7c  /opt/anvil-sampling/split-topp.patch' \
      '64aebd4552feb10b1a3120a521d9ff7df0ed199e7ebf376ee253b0c1aa3996a8  /opt/anvil-sampling/trace.patch' \
      '1125d27ad9314ddd5ee27bf3f651e18fa0adfb5c3c9364765e9e17683b7e34c6  /opt/anvil-sampling/native_comparator.py' \
      '69b39e22aca53be843b0fc2d4e5c6adf2c9d1e23e4e8d0a95dea1b9f598e64f8  /opt/anvil-sampling/reference.py' \
      '3ed8f790bf9a5d03a7de6175c34f1d8f2b91f6182e213c681fc89114e0bc6d2c  /opt/anvil-sampling/verify_patch.py' \
      '86a15934b4b0a30f4514b410a51393d822e01f272eff65cfee07df97ca298159  /opt/anvil-sampling/kernel_regression.py' \
      '24203cc50619e0b4116be44cecb14d800f6b2845298f998e1384c6adeb369cd1  /opt/anvil-sampling/launch.py' \
      'b1a9ec99af36a6eb2795ab75beae42b29d961af89d4aaf677ad4fef3f8da6694  /opt/anvil-sampling/README.md' \
    | sha256sum -c - \
    && python3 /opt/anvil-sampling/verify_patch.py --install

LABEL io.anvil-serving.patch.base-image="sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29" \
      io.anvil-serving.patch.upstream="0a1276e5e3d9000488e7590cc0957edd709f2152" \
      io.anvil-serving.patch.source-sha256="2b5305c406d8495e515f048ce0322c7ae5946a49e5f27bd5bbbf30a1e1fea0e6"

COPY 8244d1c4-marker-trace/marker-trace.patch 8244d1c4-marker-trace/source-hashes.json 8244d1c4-marker-trace/anvil_marker_trace.py 8244d1c4-marker-trace/install.py 8244d1c4-marker-trace/gpu_regression.py 8244d1c4-marker-trace/launch.py 8244d1c4-marker-trace/README.md 8244d1c4-marker-trace/UPSTREAM-LICENSE /opt/anvil-marker-trace/
RUN --network=none printf '%s\n' \
      '7efcd73a6d6633e3397c2f8d6b0f4fafc492acdcc82bf1e9eff3360ade12b098  /opt/anvil-marker-trace/marker-trace.patch' \
      'a54f06ec8c12deb47f2abd5ef0ce099233a94d8135bbfef1cc6686eb1505630b  /opt/anvil-marker-trace/source-hashes.json' \
      'bfcc8736cdeb4934febf54f6439b97084ad6331b9f47e72bd6e0c8f4434f6280  /opt/anvil-marker-trace/anvil_marker_trace.py' \
      'b91500d0bf11657911d07cab58f05b0692a736c1bf907d5addab7750380bf176  /opt/anvil-marker-trace/install.py' \
      'ecfbdb3c0ac97b83d7d244da7187bf61bb5a24140ac797bcd67cc599227eae70  /opt/anvil-marker-trace/gpu_regression.py' \
      '41bc2b275679bdcc1f8234b6a231f33b65320fa55a037a467d6fd1e0175e7ddc  /opt/anvil-marker-trace/launch.py' \
      '8ef3feaf33dfff68e3295a21d1aaf016e91a982a4ee7fa7f8bcb8441dcfcc885  /opt/anvil-marker-trace/README.md' \
      'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4  /opt/anvil-marker-trace/UPSTREAM-LICENSE' \
    | sha256sum -c - \
    && python3 /opt/anvil-marker-trace/install.py --install

LABEL io.anvil-serving.marker-trace.base-image="sha256:41c4926b08c7298fb63bc7f1204926c4f95ce9083ff610069ab442141213b1aa" \
      io.anvil-serving.marker-trace.manifest-sha256="a54f06ec8c12deb47f2abd5ef0ce099233a94d8135bbfef1cc6686eb1505630b"
