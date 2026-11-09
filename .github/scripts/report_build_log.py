"""Publish the tail of a failed Android build log where it can be read.

The APK is built on a cloud runner, and the thing that most often blocks the
build is a p4a recipe (a patch that no longer applies, a version mismatch, a
missing NDK symbol).  Those failures are text in the job log -- and the job log
is *not* readable without a repository token: ``/actions/runs/<id>/logs``
answers 403 "Must have admin rights" for the public repository, so an agent or a
contributor without write access sees only "step 7 failed".  Check-run
annotations, on the other hand, are public for a public run.

So on failure the build step leaves its output in ``buildozer.log`` and this
script turns the interesting part of it into workflow commands:

    ::error title=...::<escaped message>

GitHub turns each of those into an annotation on the check run and prints it on
the job page, which is the only channel that survives the token requirement.

Three limits shape the code below, and ignoring any of them silently loses the
one line that explains the failure:

* **Ten annotations per step.**  GitHub stores at most ten of them, so the
  *tail* -- where a failed Makefile prints its verdict -- is emitted first and
  the keyword matches only fill what is left.
* **~4 KiB per annotation.**  A build log is mostly compiler command lines, so
  each chunk is cut well below that limit and each line is shortened.
* **One line per workflow command.**  Newlines and percent signs are escaped.

buildozer also prints its complete environment on failure.  That dump is pure
volume, so it is collapsed: otherwise it fills the tail window and pushes the
traceback that explains the failure out of the annotation.

The same text goes to ``$GITHUB_STEP_SUMMARY`` for a human reading the run, and
the raw file is uploaded as an artifact by the workflow.

This script always exits 0: the build step has already failed, and a reporting
failure must not hide the real one.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Ten annotations per step: keep the tail in the first few and let keyword
# matches use the rest.  Chunks stay under GitHub's ~4 KiB annotation cap.
CHUNK = 3000
TAIL_CHUNKS = 4
ERROR_CHUNKS = 6
TAIL_LINES = 150
# Long lines used to be cut at 300 chars.  That hid the whole point of a pip or
# configure line: the failing command and its flags sit past the first 300
# characters, and only "[...]" was left in the annotation.
LINE_MAX = 700
ERROR_CONTEXT = 3
MAX_ERRORS = 30

# buildozer dumps its entire environment between the traceback and the final
# "# Command failed" verdict.  Those lines ("#     ANDROIDNDK = '...'") carry no
# diagnosis and used to fill three of the four tail chunks, pushing the actual
# exception out of the annotation entirely.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ENV_LINE = re.compile(r"^\s*#\s{2,}[A-Za-z_][A-Za-z0-9_]*\s*=\s")

# Keyword matches use whole words, so ``-Werror=implicit-function-declaration``
# in a compiler command line is not "an interesting error": it matched every
# single compile line and filled all ten annotation slots with noise.
_INTERESTING = re.compile(
    r"\b(error|Error|ERROR|fatal|failed|Failed|FAILED|FAILURE|Traceback|Exception"
    r"|undefined reference|cannot find|No such file|refused|not found)\b"
)

_TAIL_TITLE = "buildozer tail"
_ERROR_TITLE = "buildozer errors"


def _escape(text: str) -> str:
    """Escape a message for a workflow command (percent, CR and LF)."""
    return (text.replace("%", "%25")
                .replace("\r", "%0D")
                .replace("\n", "%0A"))


def _short(line: str) -> str:
    """Keep the start of a long line; compiler commands are thousands wide."""
    return line if len(line) <= LINE_MAX else line[:LINE_MAX] + " [...]"


def _strip_environment(lines: list[str]) -> list[str]:
    """Collapse buildozer's environment dump to one line.

    The dump sits between the traceback that explains the failure and the final
    ``# Command failed`` verdict, so leaving it in the tail window hides both.
    """
    out: list[str] = []
    dropped = 0
    for line in lines:
        plain = _ANSI.sub("", line)
        if _ENV_LINE.match(plain):
            dropped += 1
            continue
        if dropped:
            out.append(f"# (environment variables omitted: {dropped} lines)")
            dropped = 0
        out.append(line)
    if dropped:
        out.append(f"# (environment variables omitted: {dropped} lines)")
    return out


def _chunks(title: str, lines: list[str], limit: int) -> list[str]:
    """Split ``lines`` into annotation-sized workflow commands."""
    commands: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if buf and size + len(line) + 1 > CHUNK:
            commands.append(f"::error title={title}::{_escape(chr(10).join(buf))}")
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        commands.append(f"::error title={title}::{_escape(chr(10).join(buf))}")
    return commands[:limit]


def _error_block(lines: list[str]) -> list[str]:
    """The last few error lines with a little context around each."""
    hits = [i for i, line in enumerate(lines) if _INTERESTING.search(line)]
    if not hits:
        return []
    # Collapse adjacent hits so a traceback is printed once, not per line.
    groups: list[tuple[int, int]] = []
    for idx in hits[-MAX_ERRORS:]:
        start = max(0, idx - ERROR_CONTEXT)
        end = min(len(lines), idx + ERROR_CONTEXT + 1)
        if groups and start <= groups[-1][1]:
            groups[-1] = (groups[-1][0], max(groups[-1][1], end))
        else:
            groups.append((start, end))
    out: list[str] = []
    for start, end in groups:
        out.extend(_short(line) for line in lines[start:end])
        out.append("--")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: report_build_log.py <log file>", file=sys.stderr)
        return 0
    path = Path(argv[1])
    if not path.is_file():
        print("::error title=buildozer log::no log file at %s" % path)
        return 0

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        print("::error title=buildozer log::the build produced no output")
        return 0

    readable = _strip_environment(lines)
    commands = _chunks(_TAIL_TITLE, [_short(l) for l in readable[-TAIL_LINES:]],
                       TAIL_CHUNKS)
    commands += _chunks(_ERROR_TITLE, _error_block(readable), ERROR_CHUNKS)
    for command in commands:
        print(command)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## buildozer log (tail)\n\n```\n")
            handle.write("\n".join(readable[-TAIL_LINES:]))
            handle.write("\n```\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
