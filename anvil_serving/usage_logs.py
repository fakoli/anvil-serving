"""Bounded discovery and parsing for local Claude Code usage logs."""

import json
import os

MAX_LOG_FILES = 50_000
MAX_LOG_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_LOG_BYTES = 8 * 1024 * 1024 * 1024
MAX_LOG_LINE_BYTES = 8 * 1024 * 1024
MAX_LOG_DIRECTORIES = 100_000


def discover_jsonl_logs(root):
    """Return regular JSONL files below root within explicit scan limits."""
    root = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
    files = []
    total_bytes = 0
    directories = 0

    def fail_unreadable(error):
        raise OSError("cannot scan log directory: %s" % error) from error

    for directory, names, filenames in os.walk(
            root, followlinks=False, onerror=fail_unreadable):
        directories += 1
        if directories > MAX_LOG_DIRECTORIES:
            raise ValueError(
                "log scan exceeds %d directories" % MAX_LOG_DIRECTORIES
            )
        names[:] = sorted(
            name for name in names
            if not os.path.islink(os.path.join(directory, name))
        )
        for name in sorted(filenames):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(directory, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            if size > MAX_LOG_FILE_BYTES:
                raise ValueError(
                    "log file exceeds %d bytes: %s" % (MAX_LOG_FILE_BYTES, path)
                )
            files.append(path)
            if len(files) > MAX_LOG_FILES:
                raise ValueError("log scan exceeds %d files" % MAX_LOG_FILES)
            total_bytes += size
            if total_bytes > MAX_TOTAL_LOG_BYTES:
                raise ValueError(
                    "log scan exceeds %d aggregate bytes" % MAX_TOTAL_LOG_BYTES
                )
    return files


def iter_json_objects(path):
    """Yield JSON objects while rejecting lines large enough to exhaust memory."""
    with open(path, "rb") as handle:
        while True:
            raw = handle.readline(MAX_LOG_LINE_BYTES + 1)
            if not raw:
                return
            if len(raw) > MAX_LOG_LINE_BYTES:
                raise ValueError(
                    "log line exceeds %d bytes: %s" % (MAX_LOG_LINE_BYTES, path)
                )
            raw = raw.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw.decode("utf-8", errors="replace"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                yield value
