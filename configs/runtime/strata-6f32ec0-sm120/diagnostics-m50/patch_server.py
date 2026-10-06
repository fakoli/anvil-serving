"""Build-time patch restricted to the exact baked upstream source digest."""
import hashlib
from pathlib import Path

SOURCE_SHA256 = 'cbc211be8bf56fc1c16a187aef5633c1c6142f956fbfd9356736cc5a2c2452f5'
START = 'def echo_requests(log_path: str, offset: int) -> None:'
END = '\n\ndef experts_loading_words('
OLD_ENABLE = 'if os.environ.get("STRATA_REQUEST_LINES") and os.path.abspath(log) not in _echoing:'
NEW_ENABLE = '(os.environ.get("STRATA_REQUEST_LINES") or os.environ.get("STRATA_DIAGNOSTIC_LINES") == "1")'
REPLACEMENT = 'def echo_requests(log_path: str, offset: int) -> None:\n    """Follow the owned engine log; emit only parsed metadata, never raw lines."""\n    from anvil_diagnostics import MAX_LINE, emit_diagnostic\n\n    requests = bool(os.environ.get("STRATA_REQUEST_LINES"))\n    diagnostics = os.environ.get("STRATA_DIAGNOSTIC_LINES") == "1"\n    with open(log_path, "r", encoding="utf-8", errors="replace") as f:\n        f.seek(offset)\n        pending = ""\n        discarding = False\n        while True:\n            chunk = f.readline(MAX_LINE + 1)\n            if not chunk:\n                time.sleep(0.2)\n                continue\n            # An append may end midway through a line. Never parse partial lines,\n            # or the suffix of an overlong line after dropping its prefix.\n            if discarding:\n                discarding = not chunk.endswith("\\n")\n                continue\n            pending += chunk\n            if len(pending) > MAX_LINE:\n                pending = ""\n                discarding = not chunk.endswith("\\n")\n                continue\n            if not pending.endswith("\\n"):\n                continue\n            line, pending = pending, ""\n            if diagnostics:\n                emit_diagnostic(line)\n            m = ENGINE_REQUEST.search(line) if requests else None\n            if m:\n                read_ms, gen_ms = float(m["read"]), float(m["gen_ms"])\n                print("[strata] request prompt %s cached %s output %s prompt_read %.0f ms total %.0f ms prefill %s "\n                      "tok/s decode %s tok/s" % (m["prompt"], m["reused"], m["gen"], read_ms, read_ms + gen_ms, m["pp"],\n                                                 m["tg"]), flush=True)\n'


def patch_source(data):
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError('Pinned Strata server source identity mismatch')
    # The pinned parent contains CRLF bytes; normalize only after exact identity verification.
    text = data.decode('utf-8').replace('\r\n', '\n')
    if text.count(START) != 1 or text.count(END) != 1 or text.count(OLD_ENABLE) != 1:
        raise ValueError('Pinned Strata diagnostic patch anchors changed')
    begin = text.index(START)
    end = text.index(END, begin)
    text = text[:begin] + REPLACEMENT + text[end:]
    text = text.replace(OLD_ENABLE, 'if ' + NEW_ENABLE + ' and os.path.abspath(log) not in _echoing:')
    compile(text, 'serve/server.py', 'exec')
    return text.encode('utf-8')


def main():
    path = Path('/opt/strata/serve/server.py')
    data = patch_source(path.read_bytes())
    path.write_bytes(data)
    print('anvil_strata_diagnostic_patch_sha256=' + hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main()
