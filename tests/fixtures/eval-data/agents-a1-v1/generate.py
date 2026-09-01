"""Generate the tiny CC0 multimodal qualification fixtures with pinned FFmpeg."""

from __future__ import annotations

import os
import subprocess

IMAGE = "vllm/vllm-openai:nightly-f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1"
ROOT = os.path.dirname(os.path.abspath(__file__))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _draw(text, x, y, size=36, color="white", enable=None):
    value = (
        "drawtext=fontfile=%s:text='%s':x=%s:y=%s:fontsize=%s:fontcolor=%s"
        % (FONT, text, x, y, size, color)
    )
    if enable:
        value += ":enable='%s'" % enable
    return value


def _run(output, source, filters, *, duration=None):
    command = [
        "docker", "run", "--rm",
        "--mount", "type=bind,source=%s,target=/out" % ROOT,
        "--entrypoint", "ffmpeg", IMAGE,
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", source,
        "-vf", ",".join(filters),
        "-map_metadata", "-1", "-fflags", "+bitexact",
    ]
    if duration is None:
        command += ["-frames:v", "1", "-threads", "1", "/out/" + output]
    else:
        command += [
            "-t", str(duration), "-r", "1", "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
            "-pix_fmt", "yuv420p", "-flags:v", "+bitexact",
            "-movflags", "+faststart", "/out/" + output,
        ]
    subprocess.run(command, check=True)


def _normalize_cc_video(source, output):
    """Create a pinned MP4 derivative for the runtime compatibility lane."""
    source_path = os.path.join(ROOT, "cc", source)
    if not os.path.isfile(source_path):
        return
    command = [
        "docker", "run", "--rm",
        "--mount", "type=bind,source=%s,target=/out" % ROOT,
        "--entrypoint", "ffmpeg", IMAGE,
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", "/out/cc/" + source,
        "-map_metadata", "-1", "-fflags", "+bitexact", "-an",
        "-vf", "fps=4,scale=640:-2",
        "-c:v", "libx264", "-preset", "medium", "-crf", "28",
        "-pix_fmt", "yuv420p", "-flags:v", "+bitexact", "-threads", "1",
        "-movflags", "+faststart", "/out/cc/" + output,
    ]
    subprocess.run(command, check=True)


def main():
    image_source = "color=c=#172033:s=640x360:d=1"
    _run("scene.png", image_source, [
        "drawbox=x=60:y=220:w=520:h=80:color=#335c33:t=fill",
        "drawbox=x=90:y=155:w=110:h=65:color=red:t=fill",
        _draw("RED CAR", 82, 95),
        _draw("GREEN PARK", 330, 235, 30),
        _draw("SUNNY DAY", 210, 25, 42, "yellow"),
    ])
    _run("ocr.png", image_source, [
        _draw("ANVIL READY", 150, 90, 48, "#7CFC00"),
        _draw("CODE 42917", 180, 175, 44),
        _draw("TEMPERATURE 42 C", 110, 255, 32, "#75bfff"),
    ])
    _run("chart.png", image_source, [
        _draw("QUARTERLY UNITS", 145, 20, 36),
        "drawbox=x=100:y=230:w=90:h=80:color=#4aa3ff:t=fill",
        "drawbox=x=270:y=150:w=90:h=160:color=#ffb347:t=fill",
        "drawbox=x=440:y=70:w=90:h=240:color=#7CFC00:t=fill",
        _draw("Q1 10", 98, 315, 24),
        _draw("Q2 20", 268, 315, 24),
        _draw("Q3 30", 438, 315, 24),
    ])
    _run("ui.png", image_source, [
        _draw("ANVIL SERVING", 145, 28, 42),
        "drawbox=x=70:y=110:w=500:h=170:color=#25314a:t=fill",
        _draw("STATUS READY", 115, 135, 38, "#7CFC00"),
        _draw("GPU 42 PERCENT", 115, 205, 34, "#75bfff"),
    ])
    _run("spatial.png", image_source, [
        "drawbox=x=110:y=150:w=90:h=90:color=#4aa3ff:t=fill",
        "drawbox=x=275:y=150:w=90:h=90:color=#4aa3ff:t=fill",
        "drawbox=x=440:y=150:w=90:h=90:color=#4aa3ff:t=fill",
        _draw("COUNT THE BLUE BOXES", 95, 50, 36),
    ])
    _run("compare-a.png", image_source, [
        _draw("METER A", 225, 55, 42),
        "drawbox=x=90:y=160:w=460:h=60:color=#4a5568:t=fill",
        "drawbox=x=90:y=160:w=115:h=60:color=#ffb347:t=fill",
        _draw("25 PERCENT", 190, 260, 38),
    ])
    _run("compare-b.png", image_source, [
        _draw("METER B", 225, 55, 42),
        "drawbox=x=90:y=160:w=460:h=60:color=#4a5568:t=fill",
        "drawbox=x=90:y=160:w=345:h=60:color=#7CFC00:t=fill",
        _draw("75 PERCENT", 190, 260, 38),
    ])

    video_source = "color=c=#172033:s=320x180:r=1"
    _run("temporal-order-10s.mp4", video_source, [
        _draw("FIRST RED", 38, 62, 34, "red", "lt(t,5)"),
        _draw("THEN GREEN", 20, 62, 34, "#7CFC00", "gte(t,5)"),
    ], duration=10)
    _run("state-change-30s.mp4", video_source, [
        _draw("DOOR CLOSED", 20, 62, 30, "white", "lt(t,15)"),
        _draw("DOOR OPEN", 55, 62, 30, "#7CFC00", "gte(t,15)"),
    ], duration=30)
    _run("event-localization-60s.mp4", video_source, [
        _draw("TIMELINE RUNNING", 8, 20, 22),
        _draw("ALERT AT 42 SECONDS", 4, 76, 25, "red", "between(t,42,46)"),
    ], duration=60)
    _run("video-ocr-30s.mp4", video_source, [
        _draw("ANVIL VIDEO OCR", 12, 42, 28, "#75bfff"),
        _draw("CODE 7291", 65, 98, 32),
    ], duration=30)
    _run("continuity-120s.mp4", video_source, [
        _draw("PHASE ALPHA", 50, 70, 30, "red", "lt(t,40)"),
        _draw("PHASE BETA", 55, 70, 30, "#ffb347", "between(t,40,79)"),
        _draw("PHASE GAMMA", 40, 70, 30, "#7CFC00", "gte(t,80)"),
    ], duration=120)
    _normalize_cc_video(
        "chemical-traffic-light.webm", "chemical-traffic-light-normalized.mp4"
    )
    _normalize_cc_video("cat-opening-door.webm", "cat-opening-door-normalized.mp4")


if __name__ == "__main__":
    main()
