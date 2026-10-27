"""Bounded, decoded reference assets in the existing owned artifact store."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .errors import MediaError

ASSET_PATH = "/media/assets"
MAX_ASSET_BYTES = 32 * 1024 * 1024
MEDIA_FORMATS = {"image/png": ("png_pipe", "png"), "image/jpeg": ("jpeg_pipe", "jpg"),
                 "video/mp4": ("mov", "mp4"), "audio/wav": ("wav", "wav")}
MAXIMA = {"width": 4096, "height": 4096, "frames": 3600, "durationSeconds": 120,
          "decodedBytes": 2 * 1024**3, "channels": 2, "sampleRate": 96000}


def policy():
    return {"path": ASSET_PATH, "available": bool(shutil.which("ffprobe")),
            "method": "POST", "requiresAuthentication": True, "requiredScope": "media:submit",
            "mediaTypes": list(MEDIA_FORMATS), "maxBytes": MAX_ASSET_BYTES,
            "maxima": dict(MAXIMA), "retentionSeconds": 86400,
            "digestHeader": "X-Content-SHA256", "cli": "media asset upload"}


def enforce_limits(info, limits):
    for name, maximum in limits.items():
        value = info.get(name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or isinstance(value, float) and not math.isfinite(value) or value is None
                or value <= 0 or value > maximum):
            raise MediaError("asset_extent_invalid", "reference media exceeds a declared decoded limit", status=422)


def _probe(path, media_type, *, count=False):
    executable = shutil.which("ffprobe")
    if executable is None:
        raise MediaError("asset_probe_unavailable", "reference intake requires the managed decoder", status=503)
    argv = [executable, "-v", "error", "-max_alloc", str(64 * 1024**2),
            "-threads", "1", "-max_pixels", str(16 * 1024**2), "-protocol_whitelist", "file,pipe",
            "-f", MEDIA_FORMATS[media_type][0]]
    if media_type == "video/mp4":
        argv += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    if count:
        argv += ["-count_frames"]
    argv += ["-show_entries", "stream=codec_type,width,height,nb_frames,nb_read_frames,channels,sample_rate,duration:format=duration",
             "-of", "json", str(path)]
    # Decoder diagnostics are untrusted and never echoed. Temporary files bound Python memory.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                    timeout=60 if count else 10, check=False)
            if result.returncode or output.tell() > 65536 or errors.tell():
                raise ValueError("decode failed")
            output.seek(0)
            return json.load(output)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise MediaError("asset_decode_failed", "reference media failed bounded decoding", status=422) from exc


def inspect_media(path: Path, media_type: str):
    if media_type not in MEDIA_FORMATS:
        raise MediaError("artifact_media_type", "reference media type is unsupported", status=415)

    def metadata(raw, *, counted):
        try:
            streams = raw["streams"]
            expected = "audio" if media_type == "audio/wav" else "video"
            if not isinstance(streams, list) or not 1 <= len(streams) <= 2:
                raise ValueError("stream count")
            if any(s.get("codec_type") not in {"video", "audio"} for s in streams):
                raise ValueError("unsupported stream")
            selected = [s for s in streams if s["codec_type"] == expected]
            if len(selected) != 1 or media_type != "video/mp4" and len(streams) != 1:
                raise ValueError("stream ambiguity")
            stream = selected[0]
            info = {}
            if expected == "video":
                info.update(width=int(stream["width"]), height=int(stream["height"]))
                frames = stream.get("nb_read_frames" if counted else "nb_frames")
                if counted or frames not in {None, "N/A"}:
                    info["frames"] = int(frames)
                elif media_type.startswith("image/"):
                    info["frames"] = 1
                if "frames" in info:
                    info["decodedBytes"] = info["width"] * info["height"] * info["frames"] * 12
            if not media_type.startswith("image/"):
                info["durationSeconds"] = float(raw["format"]["duration"])
            for audio in (s for s in streams if s["codec_type"] == "audio"):
                info["channels"] = int(audio["channels"])
                info["sampleRate"] = int(audio["sample_rate"])
                if expected == "audio":
                    info["decodedBytes"] = math.ceil(info["durationSeconds"] * info["sampleRate"]) * info["channels"] * 4
            enforce_limits(info, {k: v for k, v in MAXIMA.items() if k in info})
            if media_type.startswith("image/") and info.get("frames") != 1:
                raise ValueError("animated image")
            return info
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MediaError("asset_metadata_invalid", "reference media metadata is invalid", status=422) from exc

    metadata(_probe(path, media_type), counted=False)
    return metadata(_probe(path, media_type, count=True), counted=True)


class LimitedReader:
    """Do not wait for EOF on a persistent HTTP connection."""
    def __init__(self, source, length):
        self.source, self.remaining = source, length
        self.deadline = time.monotonic() + 120

    def read(self, size):
        if not self.remaining:
            return b""
        if time.monotonic() >= self.deadline:
            raise MediaError("asset_upload_timeout", "upload exceeded its total deadline", status=408)
        data = getattr(self.source, "read1", self.source.read)(min(size, self.remaining))
        if not data:
            raise MediaError("asset_incomplete", "upload ended before Content-Length", status=400)
        self.remaining -= len(data)
        return data
