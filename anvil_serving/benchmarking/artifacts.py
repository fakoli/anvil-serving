"""Validation and atomic writes for benchmark artifacts."""

import json
import os
import sys
import tempfile


def atomic_write_json(path, value):
    """Atomically replace a JSON artifact without leaving a truncated target."""
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=parent,
                prefix=".%s." % os.path.basename(out), suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(
                value,
                handle,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, out)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def validate_write_target(path, *, label="output"):
    """Fail before live work when a requested artifact cannot be replaced safely."""
    if not path or path == "-":
        return None
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("%s directory does not exist: %s" % (label, parent))
    if os.path.islink(out):
        raise OSError("%s path cannot be a symbolic link: %s" % (label, out))
    if os.path.exists(out) and not os.path.isfile(out):
        raise OSError("%s path is not a regular file: %s" % (label, out))
    if not os.access(parent, os.W_OK):
        raise OSError("%s directory is not writable: %s" % (label, parent))
    return out


def console_safe(value):
    """Render a value without failing on a restricted console encoding."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(encoding, errors="backslashreplace").decode(encoding)
