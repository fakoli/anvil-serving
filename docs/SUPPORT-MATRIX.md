# Support matrix

This is the supported scope for Anvil Serving, as decided by the project owner:

> **Supported** = gateway on any OS; managed serving on Linux or WSL + NVIDIA + Docker.
> **Experimental** = macOS MLX/launchd.
> **Not supported** = AMD/Intel/CPU-only managed serving, systemd-native, native Windows runtimes.

"Gateway" means the Capability Gateway (`anvil-serving router run`) in front of
a model server you already run, as set up by
[the gateway quickstart](QUICKSTART-GATEWAY.md). "Managed serving" means
Anvil Serving starts and stops the model server itself (`serves`, `models
recipes`, `init` without `--gateway-only`).

| What | Platform | Status | Notes |
| --- | --- | --- | --- |
| Gateway (`router run`) | Linux | Supported | Python 3.11+. Exercised by the full CI test suite. |
| Gateway (`router run`) | macOS | Supported | Python 3.11+. |
| Gateway (`router run`) | Windows | Supported | Python 3.11+. Windows CI currently runs a CLI smoke only, not the gateway suite; WSL is the exercised path. Report Windows-specific gateway problems as issues. |
| Upstream behind the gateway | Any OpenAI-compatible server | Supported | Ollama, LM Studio, llama.cpp `llama-server`, vLLM, SGLang, or any server with `/v1/chat/completions`. The gateway does not care which engine it is. |
| Managed serving | Linux + NVIDIA GPU + Docker | Supported | Docker Engine with Compose v2 and the NVIDIA Container Toolkit. `anvil-serving doctor` checks the prerequisites. |
| Managed serving | Windows + WSL + NVIDIA GPU + Docker | Supported | Run the runtimes inside WSL or Docker Desktop's Linux engine. |
| Managed serving | macOS + Apple Silicon, MLX through launchd | Experimental | See [Host-supervised services](HOST-SERVICES.md). |
| Managed serving | AMD or Intel GPUs, CPU only | Not supported | Run your own server and use the gateway in front of it instead. |
| Service supervision | systemd-native units | Not supported | Docker and launchd are the implemented supervisors. |
| Runtimes | Native Windows (serves, controller, voice, media workers) | Not supported | The native Windows CLI can still inspect configuration and dispatch typed operations to a Linux owner; see [Getting started](GETTING-STARTED.md#windows-client). |

Anything outside this table is unsupported until it is added here. Use
`127.0.0.1`, never `localhost`, in every URL you configure.
