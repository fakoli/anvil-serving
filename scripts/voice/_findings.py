"""Shared helper: insert one markdown table row into a findings doc's
session log, for the live-hardware harness scripts in this package
(``mini_validation.py`` T016, ``local_loop_demo.py`` T010,
``realtime_sdk_client_demo.py`` T014). Each script builds its own row text
and keeps its own script-name-prefixed stderr messages; this module holds
the ONE shared "find the ``| timestamp (UTC) | ... |`` table, insert before
the ``| _TBD_ |`` sentinel row (or at the end)" implementation that used to
be copy-pasted three times.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional


def insert_finding_row(
    findings_doc: Path,
    row: str,
    *,
    script_name: str,
    require_session_log_heading: bool = True,
    missing_doc_message: Optional[str] = None,
    no_table_message: Optional[str] = None,
    write_error_message: Optional[Callable[[OSError], str]] = None,
) -> bool:
    """Insert ``row`` into ``findings_doc``'s session-log markdown table.

    Never raises: any expected failure (missing doc, missing table, an
    ``OSError`` on read/write) prints a message to stderr and returns
    ``False`` instead, so a findings-doc problem never crashes an otherwise-
    completed harness run.

    ``require_session_log_heading`` controls where the ``| timestamp (UTC) |``
    header row is searched for: after a ``## Session log`` heading (mini_validation/
    local_loop_demo's findings docs), or anywhere in the file (realtime_sdk_client_demo's,
    which has no such heading). The three optional ``*_message`` overrides let a
    caller preserve stderr wording that differs from the shared default.
    """
    row = row.rstrip("\n")
    try:
        if not findings_doc.exists():
            print(
                missing_doc_message
                if missing_doc_message is not None
                else "%s: findings doc does not exist: %s" % (script_name, findings_doc),
                file=sys.stderr,
            )
            return False

        lines = findings_doc.read_text(encoding="utf-8").splitlines()
        search_start = 0
        if require_session_log_heading:
            try:
                search_start = lines.index("## Session log") + 1
            except ValueError:
                print(
                    "%s: findings doc has no Session log heading" % script_name,
                    file=sys.stderr,
                )
                return False

        header_idx = next(
            (i for i in range(search_start, len(lines)) if lines[i].startswith("| timestamp (UTC) |")),
            None,
        )
        if header_idx is None or header_idx + 1 >= len(lines) or not lines[header_idx + 1].startswith("|---"):
            print(
                no_table_message
                if no_table_message is not None
                else "%s: findings doc has no session-log markdown table" % script_name,
                file=sys.stderr,
            )
            return False

        insert_at = header_idx + 2
        while insert_at < len(lines) and lines[insert_at].startswith("|"):
            if lines[insert_at].startswith("| _TBD_ |"):
                break
            insert_at += 1
        lines.insert(insert_at, row)
        findings_doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        print(
            write_error_message(exc)
            if write_error_message is not None
            else "%s: could not append to %s: %s" % (script_name, findings_doc, exc),
            file=sys.stderr,
        )
        return False
