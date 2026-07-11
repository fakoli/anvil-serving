from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_markdown_links.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_markdown_links", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


links = _load_module()


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return tmp_path


def test_check_accepts_relative_link_forms_and_ignores_non_file_content(tmp_path: Path):
    root = _repo(tmp_path, {
        "README.md": (
            "[guide](docs/guide.md#start)\n"
            "![image](<assets/a b.png>)\n"
            "[encoded](docs/a%20b.md?view=1)\n"
            "[external](https://example.com/missing.md)\n"
            "[protocol relative](//example.com/missing.md)\n"
            "[anchor](#local)\n"
            "[title](docs/guide.md \"Guide title\")\n"
            "\\[escaped](missing.md)\n"
            "literal](missing.md)\n"
            "`[inline code](missing.md)`\n"
            "````markdown\n```not-a-close\n[fenced](missing.md)\n````\n"
            "<!--\n```markdown\n[commented](missing.md)\n```\n-->\n"
            "\n    [indented code](missing.md)\n"
            ">     [blockquote code](missing.md)\n"
            "<pre>\n[html code](missing.md)\n</pre>\n"
            "<pre>[same-line html code](missing.md)</pre>\n"
            "[ref]: docs/guide.md 'title'\n"
            "<a href=\"docs/guide.md\">raw link</a>\n"
            "<img src=\"assets/a b.png\" alt=\"raw image\">\n"
        ),
        "docs/guide.md": "# Start\n",
        "docs/a b.md": "# Spaced\n",
        "assets/a b.png": "not really an image",
    })

    checked, problems = links.check(root)

    assert checked == 3
    assert problems == ()


def test_check_reports_missing_case_mismatch_absolute_escape_and_backslash(tmp_path: Path):
    root = _repo(tmp_path, {
        "README.md": (
            "[missing](docs/missing.md)\n"
            "[case](Docs/guide.md)\n"
            "[absolute](/etc/passwd)\n"
            "[escape](../outside.md)\n"
            "[windows](docs\\guide.md)\n"
        ),
        "docs/guide.md": "# Guide\n",
    })

    _checked, problems = links.check(root)
    messages = [problem.message for problem in problems]

    assert any("missing relative link target" in message for message in messages)
    assert any("case mismatch" in message for message in messages)
    assert any("absolute filesystem path" in message for message in messages)
    assert any("escapes the repository" in message for message in messages)
    assert any("forward slashes" in message for message in messages)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[bad](docs/%ZZ.md)\n", "invalid percent escape"),
        ("[bad](file:///tmp/item.md)\n", "file: links are not allowed"),
        ("[bad](%2Fetc/passwd)\n", "decoded link destination is absolute"),
    ],
)
def test_check_rejects_malformed_destinations(tmp_path: Path, content: str, message: str):
    root = _repo(tmp_path, {"README.md": content, "docs/guide.md": "# Guide\n"})

    _checked, problems = links.check(root)

    assert any(message in problem.message for problem in problems)


def test_untracked_markdown_does_not_change_production_scope(tmp_path: Path):
    root = _repo(tmp_path, {"README.md": "# Root\n"})
    (root / "untracked.md").write_text("[bad](missing.md)\n", encoding="utf-8")

    checked, problems = links.check(root)

    assert checked == 1
    assert problems == ()


def test_unmatched_backtick_does_not_hide_a_real_link(tmp_path: Path):
    root = _repo(tmp_path, {"README.md": "` literal [real](missing.md)\n"})

    _checked, problems = links.check(root)

    assert any("missing relative link target" in problem.message for problem in problems)


@pytest.mark.parametrize(
    "content",
    [
        ">     [blockquote code](missing.md)\n",
        "<pre>\n[html code](missing.md)\n</pre>\n",
        "<pre>[same-line html code](missing.md)</pre>\n",
    ],
)
def test_literal_code_forms_do_not_create_links(tmp_path: Path, content: str):
    root = _repo(tmp_path, {"README.md": content})

    _checked, problems = links.check(root)

    assert problems == ()


def test_real_link_after_html_code_close_is_still_checked(tmp_path: Path):
    root = _repo(tmp_path, {
        "README.md": "<pre>[code](ignored.md)</pre>[real](missing.md)\n",
    })

    _checked, problems = links.check(root)

    assert len(problems) == 1
    assert "missing.md" in problems[0].message


def test_blockquote_code_does_not_inherit_outer_paragraph_state(tmp_path: Path):
    root = _repo(tmp_path, {
        "README.md": "paragraph\n>     [blockquote code](missing.md)\n",
    })

    _checked, problems = links.check(root)

    assert problems == ()


def test_html_tag_in_inline_code_does_not_hide_real_links(tmp_path: Path):
    root = _repo(tmp_path, {
        "README.md": (
            "Use `<pre>` here. [first](missing1.md)\n"
            "[second](missing2.md)\n"
        ),
    })

    _checked, problems = links.check(root)

    assert len(problems) == 2
    assert {"missing1.md", "missing2.md"} == {
        problem.message.rsplit("'", 2)[1] for problem in problems
    }


def test_backticks_inside_raw_html_do_not_hide_the_closing_tag(tmp_path: Path):
    root = _repo(tmp_path, {
        "README.md": (
            "<pre>\n"
            "`literal backtick and closing tag </pre>\n"
            "[real](missing.md)\n"
        ),
    })

    _checked, problems = links.check(root)

    assert len(problems) == 1
    assert "missing.md" in problems[0].message


def test_git_index_decoder_is_bounded_and_rejects_duplicates():
    with pytest.raises(ValueError, match="exceeds 4 byte"):
        links._decode_git_index(b"12345", max_bytes=4)
    with pytest.raises(ValueError, match="duplicate"):
        links._decode_git_index(b"README.md\0README.md\0")


def test_markdown_reader_is_bounded(tmp_path: Path):
    path = tmp_path / "large.md"
    path.write_bytes(b"12345")

    with pytest.raises(ValueError, match="exceeds 4 byte"):
        links._read_markdown(path, max_bytes=4)


def test_rendered_link_count_is_bounded():
    content = "\n".join(
        "[x](missing.md)" for _ in range(links.MAX_RENDERED_LINKS + 1)
    )

    with pytest.raises(ValueError, match="exceeds 4096 link limit"):
        links.rendered_links(content)


def test_git_index_lookup_has_timeout(monkeypatch, tmp_path: Path):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == links.GIT_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    with pytest.raises(subprocess.TimeoutExpired):
        links.tracked_paths(tmp_path, _run=timeout)


def test_repository_markdown_links_pass():
    checked, problems = links.check(ROOT)

    assert checked > 0
    assert problems == ()
