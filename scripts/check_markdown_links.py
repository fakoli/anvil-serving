"""Check relative links rendered from every Git-tracked Markdown file.

Markdown is rendered with the same Python-Markdown/Pymdown parser family used
by MkDocs. The remaining logic is hermetic: it never fetches a URL, and Git
supplies the bounded production scope so untracked worktrees cannot affect it.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

try:
    import markdown
except ModuleNotFoundError:  # pragma: no cover - exercised in a clean environment
    markdown = None


MAX_GIT_INDEX_BYTES = 8 * 1024 * 1024
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_RENDERED_BYTES = 8 * 1024 * 1024
MAX_RENDERED_LINKS = 4_096
GIT_TIMEOUT_SECONDS = 10
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_BAD_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")


@dataclass(frozen=True)
class Link:
    target: str
    line: int
    column: int


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    column: int
    message: str


class _RenderedLinkParser(HTMLParser):
    def __init__(self, *, max_links: int) -> None:
        super().__init__(convert_charrefs=True)
        self.targets: list[str] = []
        self.max_links = max_links

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        for name, value in attrs:
            if name.casefold() in {"href", "src"} and value is not None:
                self.targets.append(value)
                if len(self.targets) > self.max_links:
                    raise ValueError(
                        f"rendered Markdown exceeds {self.max_links} link limit"
                    )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _decode_git_index(payload: bytes, *, max_bytes: int = MAX_GIT_INDEX_BYTES) -> tuple[str, ...]:
    if len(payload) > max_bytes:
        raise ValueError(f"Git index exceeds {max_bytes} byte limit")
    values = [value.decode("utf-8").replace("\\", "/") for value in payload.split(b"\0") if value]
    if len(values) != len(set(values)):
        raise ValueError("Git index contains duplicate paths")
    for value in values:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Git index contains unsafe path: {value!r}")
    return tuple(values)


def tracked_paths(root: Path, *, _run=subprocess.run) -> tuple[str, ...]:
    completed = _run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git index is unavailable: {stderr or 'git ls-files failed'}")
    return _decode_git_index(completed.stdout)


def _read_markdown(path: Path, *, max_bytes: int = MAX_MARKDOWN_BYTES) -> str:
    if path.is_symlink():
        raise ValueError("tracked Markdown symlinks are not allowed")
    with path.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"Markdown file exceeds {max_bytes} byte limit")
    return payload.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def rendered_links(
    text: str,
    *,
    max_bytes: int = MAX_RENDERED_BYTES,
    max_links: int = MAX_RENDERED_LINKS,
) -> tuple[Link, ...]:
    if markdown is None:
        raise ValueError(
            "Markdown checker dependencies are unavailable; install .[dev] or requirements-docs.txt"
        )
    try:
        rendered = markdown.markdown(text, extensions=["pymdownx.superfences"])
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError(
            "Markdown checker dependencies are unavailable; install .[dev] or requirements-docs.txt"
        ) from exc
    if len(rendered.encode("utf-8")) > max_bytes:
        raise ValueError(f"rendered Markdown exceeds {max_bytes} byte limit")
    parser = _RenderedLinkParser(max_links=max_links)
    parser.feed(rendered)
    parser.close()
    # Python-Markdown does not expose source spans. Avoid presenting rendered
    # HTML positions as Markdown locations or rescanning the source per link.
    return tuple(Link(target, 0, 0) for target in parser.targets)


def _tracked_directories(paths: tuple[str, ...]) -> set[str]:
    directories = {"."}
    for value in paths:
        parent = PurePosixPath(value).parent
        while str(parent) != ".":
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _local_target(source: str, target: str) -> str | None:
    target = target.strip()
    if not target or target.startswith("#") or target.startswith("?"):
        return None
    if target.startswith("//"):
        return None
    if _WINDOWS_ABSOLUTE_RE.match(target) or target.startswith("/"):
        raise ValueError("absolute filesystem path is not allowed")

    parsed = urlsplit(target)
    if parsed.scheme:
        if parsed.scheme.casefold() == "file":
            raise ValueError("file: links are not allowed")
        return None
    if parsed.netloc:
        return None
    if _BAD_PERCENT_RE.search(parsed.path):
        raise ValueError("invalid percent escape in link destination")
    path = unquote(parsed.path)
    if any(ord(char) < 32 for char in path):
        raise ValueError("control character in link destination")
    if "\\" in path:
        raise ValueError("relative Markdown links must use forward slashes")
    if not path or path == ".":
        return None
    if path.startswith("/") or _WINDOWS_ABSOLUTE_RE.match(path):
        raise ValueError("decoded link destination is absolute")

    parts = list(PurePosixPath(source).parent.parts)
    for part in PurePosixPath(path).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError("link destination escapes the repository")
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts).as_posix()


def check(root: Path, *, max_markdown_bytes: int = MAX_MARKDOWN_BYTES) -> tuple[int, tuple[Problem, ...]]:
    root = root.resolve()
    tracked = tracked_paths(root)
    tracked_set = set(tracked)
    directories = _tracked_directories(tracked)
    folded: dict[str, set[str]] = {}
    for value in tracked_set | directories:
        folded.setdefault(value.casefold(), set()).add(value)

    markdown_paths = tuple(path for path in tracked if path.casefold().endswith(".md"))
    problems = []
    for relative in markdown_paths:
        try:
            text = _read_markdown(
                root / Path(*PurePosixPath(relative).parts),
                max_bytes=max_markdown_bytes,
            )
            links = rendered_links(text)
        except (OSError, UnicodeError, ValueError) as exc:
            problems.append(Problem(relative, 0, 0, str(exc)))
            continue
        for link in links:
            try:
                target = _local_target(relative, link.target)
            except ValueError as exc:
                problems.append(Problem(relative, link.line, link.column, f"{exc}: {link.target!r}"))
                continue
            if target is None or target in tracked_set or target in directories:
                continue
            matches = folded.get(target.casefold(), set())
            if matches:
                message = f"link target case mismatch: {target!r}; tracked as {sorted(matches)!r}"
            else:
                message = f"missing relative link target: {target!r}"
            problems.append(Problem(relative, link.line, link.column, message))
    return len(markdown_paths), tuple(problems)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        checked, problems = check(args.root)
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "checked": 0, "error": str(exc)}, sort_keys=True))
        else:
            print(f"Markdown link check failed: {exc}", file=sys.stderr)
        return 1

    payload = {
        "ok": not problems,
        "checked": checked,
        "problems": [problem.__dict__ for problem in problems],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif problems:
        for problem in problems:
            location = problem.path
            if problem.line:
                location += f":{problem.line}:{problem.column}"
            print(f"{location}: {problem.message}", file=sys.stderr)
        print(f"checked {checked} tracked Markdown files; found {len(problems)} problem(s)", file=sys.stderr)
    else:
        print(f"checked {checked} tracked Markdown files; relative links ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
