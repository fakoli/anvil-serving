"""anvil-serving voice — up / down / run / benchmark (anvil task T001).

The voice-pipeline verb for the anvil-style stdlib orchestrator described in
``docs/findings/2026-07-04-hf-speech-to-speech-review.md``: a realtime
VAD -> STT -> LLM -> TTS pipeline that talks to three wires — an STT serve, a
TTS serve, and the anvil router (Chat Completions) for the brain — instead of
running any of that in-process.

T001/T002 shipped manifest loading + validation + this CLI skeleton.
`up`/`down` (T006/T008) now bring up/tear down the STT and TTS serves via
`anvil_serving.voice.serves` -- which itself delegates to
`anvil_serving.serves`'s declarative docker-manifest lifecycle, so there is
still no raw `docker run` in this file. `benchmark` (T015) now replays one
turn end-to-end via `anvil_serving.voice.benchmark` and prints the
TTFA/latency/WER/RTF metrics as JSON. `run`'s realtime `http.server`/`asyncio`
server is still a TODO for a later unit.

Nothing here is proven against real audio hardware, a GPU, or a live STT/TTS
serve -- see the module docstring honesty notes in `voice/serves/`,
`voice/stages/stt.py`/`tts.py`, and `voice/benchmark.py`. `up`/`down`/
`benchmark` degrade gracefully (a clear message, not a crash) when the
serves manifest isn't configured yet or the serves aren't reachable.
"""
import argparse
import json
import sys

from . import benchmark as voice_benchmark
from . import config as voice_config
from .serves import stt as stt_serve
from .serves import tts as tts_serve
from .serves._common import ServeNotConfigured

DEFAULT_MANIFEST = voice_config.DEFAULT_CONFIG

# (kind, ServeConfig class, Serve class) -- drives the bring-up/tear-down loop
# in cmd_up/cmd_down; keeps them from repeating themselves per-serve-kind.
_AUDIO_SERVES = (
    ("stt", stt_serve.STTServeConfig, stt_serve.STTServe),
    ("tts", tts_serve.TTSServeConfig, tts_serve.TTSServe),
)


def _audio_serves(voice: dict):
    for kind, config_cls, serve_cls in _AUDIO_SERVES:
        table = voice.get(kind, {})
        config = config_cls(base_url=table.get("base_url", ""), model=table.get("model", ""))
        yield kind, serve_cls(config)


def _load(config_path):
    """Load + validate the manifest; returns (data, None) or (None, error message)."""
    try:
        return voice_config.load_manifest(config_path), None
    except voice_config.ConfigError as exc:
        return None, str(exc)


def cmd_up(args):
    data, err = _load(args.config)
    if err:
        print("voice up: %s" % err, file=sys.stderr)
        return 2
    print("voice up: manifest OK -- %s" % voice_config.describe(data))
    voice = data.get("voice", {})
    exit_code = 0
    for kind, serve in _audio_serves(voice):
        try:
            rc = serve.bring_up()
            print("voice up: %s serve bring-up rc=%s" % (kind, rc))
            if rc != 0:
                exit_code = 1
        except ServeNotConfigured as exc:
            print("voice up: %s serve not configured yet -- %s" % (kind, exc))
    print("voice up: TODO -- the realtime server process itself ships in a later unit.")
    return exit_code


def cmd_down(args):
    data, err = _load(args.config)
    if err:
        print("voice down: %s" % err, file=sys.stderr)
        return 2
    print("voice down: manifest OK -- %s" % voice_config.describe(data))
    voice = data.get("voice", {})
    exit_code = 0
    for kind, serve in _audio_serves(voice):
        try:
            rc = serve.tear_down()
            print("voice down: %s serve tear-down rc=%s" % (kind, rc))
            if rc != 0:
                exit_code = 1
        except ServeNotConfigured as exc:
            print("voice down: %s serve not configured yet -- %s" % (kind, exc))
    print("voice down: TODO -- realtime-server process teardown ships in a later unit.")
    return exit_code


def cmd_run(args):
    data, err = _load(args.config)
    if err:
        print("voice run: %s" % err, file=sys.stderr)
        return 2
    voice = data.get("voice", {})
    print("voice run: manifest OK -- %s" % voice_config.describe(data))
    print(
        "voice run: TODO -- the realtime server (ws://%s:%s/v1/realtime) is not yet "
        "implemented; this is the T001/T002 foundation unit only." % (
            voice.get("realtime_host", "127.0.0.1"), voice.get("realtime_port", 8765),
        )
    )
    return 0


def cmd_benchmark(args):
    data, err = _load(args.config)
    if err:
        print("voice benchmark: %s" % err, file=sys.stderr)
        return 2
    print("voice benchmark: manifest OK -- %s" % voice_config.describe(data))
    try:
        result = voice_benchmark.run_benchmark_from_manifest(data)
    except Exception as exc:  # noqa: BLE001 - the configured serves may simply not be up yet
        print(
            "voice benchmark: could not reach the configured STT/LLM/TTS serves (%s); "
            "bring them up with `anvil-serving voice up` first. Nothing was measured." % exc
        )
        return 0
    print(json.dumps(result, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="anvil-serving voice",
        description="Voice-pipeline verb: an anvil-style stdlib VAD->STT->LLM->TTS "
                    "orchestrator + realtime server (see "
                    "docs/findings/2026-07-04-hf-speech-to-speech-review.md).",
    )
    sub = p.add_subparsers(dest="action", required=True)

    def add_config(sp):
        sp.add_argument(
            "--config",
            help="voice manifest TOML; defaults to the shipped example when present (%s)"
            % DEFAULT_MANIFEST,
        )

    sp = sub.add_parser("up", help="bring up the STT/TTS serves + realtime server (validates manifest)")
    add_config(sp)

    sp = sub.add_parser("down", help="tear down the STT/TTS serves + realtime server")
    add_config(sp)

    sp = sub.add_parser("run", help="run the realtime server in the foreground")
    add_config(sp)

    sp = sub.add_parser("benchmark", help="replay a recorded session end-to-end and report latency")
    add_config(sp)

    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    handlers = {
        "up": cmd_up,
        "down": cmd_down,
        "run": cmd_run,
        "benchmark": cmd_benchmark,
    }
    return handlers[args.action](args)


if __name__ == "__main__":
    raise SystemExit(main())
