ARG BASE_IMAGE=vllm/vllm-openai@sha256:212a1bd7b4267c604408d17dc0048ef152101bc67fbe6ba8567899fc1f227bcd
FROM ${BASE_IMAGE}

COPY configs/kernel-tunes/vllm/f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1/nvidia-rtx-pro-6000-blackwell-max-q-workstation-edition/moe-config.json /opt/anvil/kernel-tunes/E=256,N=512,device_name=NVIDIA_RTX_PRO_6000_Blackwell_Max-Q_Workstation_Edition,dtype=fp8_w8a8.json
