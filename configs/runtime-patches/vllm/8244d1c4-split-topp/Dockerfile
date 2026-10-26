FROM ghcr.io/local-inference-lab/vllm:release-10152026@sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29

COPY split-topp.patch trace.patch verify_patch.py reference.py kernel_regression.py launch.py README.md /opt/anvil-sampling/
RUN --network=none printf '%s\n' \
      'c30ca1225f4a3b36b1e015738d9cf34129b1876851122d9da1abea1e5097fc7c  /opt/anvil-sampling/split-topp.patch' \
      '64aebd4552feb10b1a3120a521d9ff7df0ed199e7ebf376ee253b0c1aa3996a8  /opt/anvil-sampling/trace.patch' \
      'e0a1dfe2f7f5aea547d38c60b69f03349bd14f3a0f64b25e5f836e3740f5f60f  /opt/anvil-sampling/reference.py' \
      '3ed8f790bf9a5d03a7de6175c34f1d8f2b91f6182e213c681fc89114e0bc6d2c  /opt/anvil-sampling/verify_patch.py' \
      '9798b88ceaa95aba5d122bda7349c71469d8b6c395b588677a1e166e59fe69c1  /opt/anvil-sampling/kernel_regression.py' \
      '24203cc50619e0b4116be44cecb14d800f6b2845298f998e1384c6adeb369cd1  /opt/anvil-sampling/launch.py' \
      '4ea5cc4aa7330236b645ce3c8489525a0685fbef20168b56135b996d1bec9733  /opt/anvil-sampling/README.md' \
    | sha256sum -c - \
    && python3 /opt/anvil-sampling/verify_patch.py --install

LABEL io.anvil-serving.patch.base-image="sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29" \
      io.anvil-serving.patch.upstream="0a1276e5e3d9000488e7590cc0957edd709f2152" \
      io.anvil-serving.patch.source-sha256="2b5305c406d8495e515f048ce0322c7ae5946a49e5f27bd5bbbf30a1e1fea0e6"
