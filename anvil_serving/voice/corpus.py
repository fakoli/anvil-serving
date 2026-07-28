"""Deterministic corpus preparation and validation for STT qualification."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator


CORPUS_SCHEMA_VERSION = "stt-corpus/v1"
LIBRISPEECH_LICENSE = "CC BY 4.0"
LIBRISPEECH_SOURCE_URL = "https://www.openslr.org/12"
LIBRISPEECH_ARCHIVES = {
    "test-clean": "https://www.openslr.org/resources/12/test-clean.tar.gz",
    "test-other": "https://www.openslr.org/resources/12/test-other.tar.gz",
}
LIBRISPEECH_MD5_URL = "https://www.openslr.org/resources/12/md5sum.txt"
SYNTHETIC_PHRASES = (
    ("weather", "What is the weather in Seattle tomorrow morning?"),
    ("timer", "Set a timer for twelve minutes and thirty seconds."),
    ("zip-code", "Find a pharmacy near ZIP code nine eight one zero nine."),
    ("date-time", "Schedule it for Thursday, August fourteenth at three forty-five P.M."),
    ("extension", "Call the front desk at extension four zero seven."),
    ("correction-cancellation", "Change that to seven thirty, no, cancel the reminder."),
)


class CorpusError(ValueError):
    """Raised when corpus input is incomplete, unsafe, or malformed."""


@dataclass(frozen=True)
class AudioMetadata:
    format: str
    sample_rate: int
    channels: int
    duration_seconds: float


@dataclass(frozen=True)
class CorpusCase:
    schema_version: str
    id: str
    audio_path: str
    reference_text: str
    category: str
    language: str
    source_identity: str
    license: str
    sha256: str


def sha256_file(path: os.PathLike[str] | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wav_metadata(path: Path) -> AudioMetadata:
    try:
        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            sample_rate = stream.getframerate()
            frames = stream.getnframes()
    except (wave.Error, EOFError) as exc:
        raise CorpusError("invalid WAV audio %s: %s" % (path, exc)) from exc
    return AudioMetadata("wav", sample_rate, channels, frames / sample_rate if sample_rate else 0.0)


def _flac_metadata(path: Path) -> AudioMetadata:
    try:
        with open(path, "rb") as stream:
            if stream.read(4) != b"fLaC":
                raise CorpusError("invalid FLAC signature: %s" % path)
            while True:
                header = stream.read(4)
                if len(header) != 4:
                    raise CorpusError("FLAC STREAMINFO block missing: %s" % path)
                is_last = bool(header[0] & 0x80)
                block_type = header[0] & 0x7F
                length = int.from_bytes(header[1:4], "big")
                payload = stream.read(length)
                if len(payload) != length:
                    raise CorpusError("truncated FLAC metadata: %s" % path)
                if block_type == 0:
                    if length != 34:
                        raise CorpusError("invalid FLAC STREAMINFO length: %s" % path)
                    packed = int.from_bytes(payload[10:18], "big")
                    sample_rate = (packed >> 44) & 0xFFFFF
                    channels = ((packed >> 41) & 0x7) + 1
                    total_samples = packed & ((1 << 36) - 1)
                    return AudioMetadata(
                        "flac",
                        sample_rate,
                        channels,
                        total_samples / sample_rate if sample_rate else 0.0,
                    )
                if is_last:
                    raise CorpusError("FLAC STREAMINFO block missing: %s" % path)
    except OSError as exc:
        raise CorpusError("cannot read FLAC audio %s: %s" % (path, exc)) from exc


def audio_metadata(path: os.PathLike[str] | str) -> AudioMetadata:
    audio = Path(path)
    suffix = audio.suffix.lower()
    if suffix == ".wav":
        metadata = _wav_metadata(audio)
    elif suffix == ".flac":
        metadata = _flac_metadata(audio)
    else:
        raise CorpusError("unsupported audio format for %s; expected .wav or .flac" % audio)
    if metadata.sample_rate != 16000 or metadata.channels != 1:
        raise CorpusError(
            "%s must be 16-kHz mono audio; found %s Hz and %s channel(s)"
            % (audio, metadata.sample_rate, metadata.channels)
        )
    if metadata.duration_seconds <= 0:
        raise CorpusError("%s has no audio samples" % audio)
    return metadata


def _safe_audio_path(manifest: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise CorpusError("audio_path must be a non-empty relative path")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise CorpusError("audio_path must be relative to the manifest: %s" % relative)
    root = manifest.parent.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CorpusError("audio_path escapes the manifest directory: %s" % relative) from exc
    return resolved


def _case_from_object(manifest: Path, line_number: int, obj: object) -> CorpusCase:
    if not isinstance(obj, dict):
        raise CorpusError("line %d must be a JSON object" % line_number)
    required = (
        "schema_version",
        "id",
        "audio_path",
        "reference_text",
        "category",
        "language",
        "source_identity",
        "license",
        "sha256",
    )
    missing = [key for key in required if key not in obj]
    if missing:
        raise CorpusError("line %d missing fields: %s" % (line_number, ", ".join(missing)))
    if obj["schema_version"] != CORPUS_SCHEMA_VERSION:
        raise CorpusError(
            "line %d schema_version must be %s" % (line_number, CORPUS_SCHEMA_VERSION)
        )
    for key in required[1:]:
        if not isinstance(obj[key], str) or not obj[key].strip():
            raise CorpusError("line %d %s must be a non-empty string" % (line_number, key))
    digest = obj["sha256"].lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise CorpusError("line %d sha256 must be 64 lowercase hexadecimal characters" % line_number)
    audio = _safe_audio_path(manifest, obj["audio_path"])
    if not audio.is_file():
        raise CorpusError("line %d audio file not found: %s" % (line_number, audio))
    audio_metadata(audio)
    actual = sha256_file(audio)
    if actual != digest:
        raise CorpusError(
            "line %d hash mismatch for %s: expected %s, got %s"
            % (line_number, obj["audio_path"], digest, actual)
        )
    return CorpusCase(**{key: obj[key] for key in required})


def validate_corpus(
    manifest_path: os.PathLike[str] | str,
    *,
    expected_cases: int | None = None,
) -> dict:
    manifest = Path(manifest_path).resolve()
    if not manifest.is_file():
        raise CorpusError("corpus manifest not found: %s" % manifest)
    cases: list[CorpusCase] = []
    seen: set[str] = set()
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CorpusError("cannot read corpus manifest %s: %s" % (manifest, exc)) from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise CorpusError("line %d is blank; JSONL records must be contiguous" % line_number)
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusError("line %d is not valid JSON: %s" % (line_number, exc)) from exc
        case = _case_from_object(manifest, line_number, obj)
        if case.id in seen:
            raise CorpusError("duplicate corpus id on line %d: %s" % (line_number, case.id))
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise CorpusError("corpus manifest contains no cases")
    if expected_cases is not None and len(cases) != expected_cases:
        raise CorpusError("corpus must contain %d cases; found %d" % (expected_cases, len(cases)))
    category_counts: dict[str, int] = {}
    total_seconds = 0.0
    for case in cases:
        category_counts[case.category] = category_counts.get(case.category, 0) + 1
        total_seconds += audio_metadata(_safe_audio_path(manifest, case.audio_path)).duration_seconds
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "manifest": str(manifest),
        "manifest_sha256": sha256_file(manifest),
        "case_count": len(cases),
        "category_counts": dict(sorted(category_counts.items())),
        "duration_seconds": round(total_seconds, 6),
        "cases": cases,
    }


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "anvil-serving-stt-corpus/1"})
    with urllib.request.urlopen(request, timeout=120) as response, open(destination, "wb") as target:  # noqa: S310
        shutil.copyfileobj(response, target, length=1024 * 1024)


def _expected_md5s() -> dict[str, str]:
    request = urllib.request.Request(
        LIBRISPEECH_MD5_URL,
        headers={"User-Agent": "anvil-serving-stt-corpus/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        text = response.read().decode("utf-8")
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            result[os.path.basename(parts[1])] = parts[0].lower()
    return result


def _md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            resolved = (root / member.name).resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise CorpusError("archive member escapes extraction root: %s" % member.name) from exc
        tar.extractall(destination, members=members, filter="data")


def _transcripts(split_root: Path) -> Iterator[tuple[str, str, Path, float]]:
    for transcript in sorted(split_root.rglob("*.trans.txt")):
        for raw in transcript.read_text(encoding="utf-8").splitlines():
            utterance_id, separator, reference = raw.partition(" ")
            if not separator or not reference.strip():
                raise CorpusError("malformed LibriSpeech transcript line in %s" % transcript)
            audio = transcript.parent / ("%s.flac" % utterance_id)
            if not audio.is_file():
                raise CorpusError("LibriSpeech audio missing for %s" % utterance_id)
            duration = audio_metadata(audio).duration_seconds
            yield utterance_id, reference.strip(), audio, duration


def _duration_bucket(seconds: float) -> str:
    if seconds < 5.0:
        return "short"
    if seconds <= 10.0:
        return "medium"
    return "long"


def select_librispeech_cases(
    records: Iterable[tuple[str, str, Path, float]],
    *,
    count: int = 12,
) -> list[tuple[str, str, Path, float]]:
    if count % 3:
        raise CorpusError("LibriSpeech selection count must divide evenly across three durations")
    records = list(records)
    per_bucket = count // 3
    selected: list[tuple[str, str, Path, float]] = []
    for bucket in ("short", "medium", "long"):
        eligible = sorted(
            (item for item in records if _duration_bucket(item[3]) == bucket),
            key=lambda item: (item[0].split("-", 1)[0], item[3], item[0]),
        )
        chosen: list[tuple[str, str, Path, float]] = []
        speakers: set[str] = set()
        for item in eligible:
            speaker = item[0].split("-", 1)[0]
            if speaker not in speakers:
                chosen.append(item)
                speakers.add(speaker)
            if len(chosen) == per_bucket:
                break
        if len(chosen) < per_bucket:
            for item in eligible:
                if item not in chosen:
                    chosen.append(item)
                if len(chosen) == per_bucket:
                    break
        if len(chosen) != per_bucket:
            raise CorpusError("not enough %s LibriSpeech cases; needed %d" % (bucket, per_bucket))
        selected.extend(chosen)
    return selected


def _write_wav(path: Path, pcm: bytes, *, sample_rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(pcm)


def _write_manifest(path: Path, cases: Iterable[CorpusCase]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        for case in cases:
            stream.write(json.dumps(asdict(case), sort_keys=True, ensure_ascii=False))
            stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def prepare_corpus(
    output_dir: os.PathLike[str] | str,
    *,
    synthesize: Callable[[str], bytes],
    transcode_flac: Callable[[Path, Path], None],
    download_dir: os.PathLike[str] | str | None = None,
) -> dict:
    """Build the fixed 24-human/6-synthetic English corpus transactionally."""
    output = Path(output_dir).resolve()
    if output.exists():
        raise CorpusError("output directory already exists: %s" % output)
    output.parent.mkdir(parents=True, exist_ok=True)
    downloads = Path(download_dir).resolve() if download_dir else output.parent / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    expected_md5 = _expected_md5s()
    stage: Path | None = Path(tempfile.mkdtemp(prefix=".%s." % output.name, dir=output.parent))
    try:
        audio_root = stage / "audio"
        audio_root.mkdir()
        cases: list[CorpusCase] = []
        archive_identities: dict[str, dict[str, str]] = {}
        for split, url in LIBRISPEECH_ARCHIVES.items():
            filename = os.path.basename(url)
            archive = downloads / filename
            if not archive.is_file():
                _download(url, archive)
            wanted = expected_md5.get(filename)
            if not wanted:
                raise CorpusError("OpenSLR checksum listing omitted %s" % filename)
            actual = _md5_file(archive)
            if actual != wanted:
                raise CorpusError(
                    "OpenSLR checksum mismatch for %s: expected %s, got %s"
                    % (filename, wanted, actual)
                )
            extract_root = stage / ("extract-%s" % split)
            extract_root.mkdir()
            _safe_extract(archive, extract_root)
            source_root = extract_root / "LibriSpeech" / split
            chosen = select_librispeech_cases(list(_transcripts(source_root)))
            split_out = audio_root / split
            split_out.mkdir()
            for utterance_id, reference, source_audio, _duration in chosen:
                destination = split_out / ("%s.wav" % utterance_id)
                transcode_flac(source_audio, destination)
                audio_metadata(destination)
                relative = destination.relative_to(stage).as_posix()
                cases.append(
                    CorpusCase(
                        CORPUS_SCHEMA_VERSION,
                        "librispeech-%s-%s" % (split, utterance_id),
                        relative,
                        reference,
                        "librispeech-%s" % split,
                        "en",
                        "%s#%s/%s" % (LIBRISPEECH_SOURCE_URL, split, utterance_id),
                        LIBRISPEECH_LICENSE,
                        sha256_file(destination),
                    )
                )
            archive_identities[split] = {
                "url": url,
                "filename": filename,
                "md5": actual,
            }

        synthetic_out = audio_root / "kokoro-agent"
        synthetic_out.mkdir()
        for index, (category, phrase) in enumerate(SYNTHETIC_PHRASES, start=1):
            pcm = synthesize(phrase)
            if not isinstance(pcm, bytes) or not pcm:
                raise CorpusError("Kokoro returned no PCM for synthetic case %s" % category)
            destination = synthetic_out / ("agent-%02d-%s.wav" % (index, category))
            _write_wav(destination, pcm)
            audio_metadata(destination)
            cases.append(
                CorpusCase(
                    CORPUS_SCHEMA_VERSION,
                    "kokoro-agent-%02d-%s" % (index, category),
                    destination.relative_to(stage).as_posix(),
                    phrase,
                    "synthetic-%s" % category,
                    "en",
                    "kokoro-live-generated",
                    "generated test fixture; Kokoro model license applies",
                    sha256_file(destination),
                )
            )

        manifest = stage / "manifest.jsonl"
        _write_manifest(manifest, cases)
        provenance = {
            "schema_version": CORPUS_SCHEMA_VERSION,
            "selection": "4 short, 4 medium, and 4 long utterances per LibriSpeech split; "
            "speaker-diverse deterministic ordering",
            "archives": archive_identities,
            "synthetic_generator": "Kokoro through configured OpenAI-compatible TTS endpoint",
            "audio_normalization": "selected LibriSpeech FLAC decoded to 16-kHz mono WAV",
        }
        (stage / "provenance.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        validation = validate_corpus(manifest, expected_cases=30)
        os.replace(stage, output)
        stage = None
        validation["manifest"] = str(output / "manifest.jsonl")
        return validation
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)


def ffmpeg_transcoder(executable: os.PathLike[str] | str) -> Callable[[Path, Path], None]:
    command = os.fspath(executable)
    if not command:
        raise CorpusError("FFmpeg executable path is required")

    def transcode(source: Path, destination: Path) -> None:
        try:
            result = subprocess.run(
                [
                    command,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-y",
                    str(destination),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CorpusError("FFmpeg failed for %s: %s" % (source, exc)) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise CorpusError(
                "FFmpeg failed for %s with rc=%d: %s"
                % (source, result.returncode, detail[-1000:])
            )

    return transcode
