"""Closed identity contract for the pinned, opt-in GPU marker diagnostic."""

MARKER_ENTRYPOINT = ["python3", "/opt/anvil-marker-trace/launch.py", "vllm", "serve"]
MARKER_MANIFEST_SHA256 = 'a54f06ec8c12deb47f2abd5ef0ce099233a94d8135bbfef1cc6686eb1505630b'
MARKER_SOURCE_FILES = {'v1/worker/gpu/model_runner.py': 'e9a5a199d0c12b7ab3c5f9bf695933b83e36edfafefbf8e2cb3edf2b6f976e2e',
 'v1/worker/gpu/sample/anvil_marker_trace.py': 'bfcc8736cdeb4934febf54f6439b97084ad6331b9f47e72bd6e0c8f4434f6280',
 'v1/worker/gpu/sample/sampler.py': '37263a79c8527955fe150102b2ac150e023535c55a7b1c1fa71d62c90d797c43',
 'v1/worker/gpu/sample/thinking_budget.py': 'df13a214922325296a00a7726bf3951ba942c47718a8db84f46c88e6f727ff3f',
 'v1/worker/gpu/spec_decode/rejection_sampler.py': 'a95a8115117e76920bc8992ad772f676dfb12676fd4cdee0479007b1f657f748',
 'v1/worker/gpu/states.py': 'a60d4227b338417752c877be03340e3b26360ecc13d122e53be7264e39a3e111'}
MARKER_HELPER_FILES = {'anvil_marker_trace.py': 'bfcc8736cdeb4934febf54f6439b97084ad6331b9f47e72bd6e0c8f4434f6280',
 'baseline-thinking_budget.py': '020513e1d3406f367de6ad995a95055008fd0c28f2873647e8283c9870e22404',
 'gpu_regression.py': 'ecfbdb3c0ac97b83d7d244da7187bf61bb5a24140ac797bcd67cc599227eae70',
 'install.py': 'b91500d0bf11657911d07cab58f05b0692a736c1bf907d5addab7750380bf176',
 'launch.py': '41bc2b275679bdcc1f8234b6a231f33b65320fa55a037a467d6fd1e0175e7ddc',
 'marker-trace.patch': '7efcd73a6d6633e3397c2f8d6b0f4fafc492acdcc82bf1e9eff3360ade12b098',
 'source-hashes.json': 'a54f06ec8c12deb47f2abd5ef0ce099233a94d8135bbfef1cc6686eb1505630b'}


def validate_marker_trace(plan):
    marker = plan.get("marker_trace")
    if marker is None:
        return None
    expected = {"schema": "anvil-serving.marker-trace/v1",
                "manifest_sha256": MARKER_MANIFEST_SHA256}
    if marker != expected or not isinstance(plan.get("runtime_patch"), dict):
        raise ValueError("unsupported marker trace declaration")
    return marker
