# Laguna RTX PRO 6000 reproduction record

The durable recipes are the digest-pinned `cand-laguna-xs-21-nvfp4` and
`cand-laguna-xs-21-nvfp4-sglang` services in
`examples/fakoli-dark/docker-compose.experiment.yml`. At final evidence capture,
Docker inspection retained these exact argument vectors:

```text
vllm serve poolside/Laguna-XS-2.1-NVFP4 --revision 07133fb3df1cc3111478e24ee71a823a598c8c2f --served-model-name laguna-xs-2.1-nvfp4 --trust-remote-code --language-model-only --reasoning-parser poolside_v1 --enable-auto-tool-choice --tool-call-parser poolside_v1 --default-chat-template-kwargs {"enable_thinking":false} --no-enable-prefix-caching --kv-cache-dtype fp8 --max-model-len 131072 --max-num-seqs 5 --gpu-memory-utilization 0.92 --host 0.0.0.0 --port 39034 --kv-cache-dtype-skip-layers 0 1 ... 39

python3 -m sglang.launch_server --model-path poolside/Laguna-XS-2.1-NVFP4 --revision 07133fb3df1cc3111478e24ee71a823a598c8c2f --served-model-name laguna-xs-2.1-nvfp4-sglang --chat-template <pinned-snapshot>/chat_template.jinja --reasoning-parser poolside_v1 --tool-call-parser poolside_v1 --trust-remote-code --context-length 262144 --max-running-requests 5 --mem-fraction-static 0.92 --host 0.0.0.0 --port 39035 --moe-runner-backend flashinfer_trtllm --disable-prefill-cuda-graph
```

The first command is the retained 131K skip-layer attempt, not the earlier
healthy FP8-KV run. The second is the retained forced-runner failure. Exact
commands and response bodies for the earlier healthy-but-wrong vLLM/SGLang
runs were not preserved before container recreation. Their summary is therefore
operator-observed, incomplete evidence and must not be used as a reproducible
quality score. The retained logs independently substantiate only the startup
stall and SGLang assertion.
