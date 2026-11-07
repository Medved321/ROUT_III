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

# A workflow command has to fit on one line, and GitHub truncates a very long
# annotation anyway.  Chunking keeps every line readable instead of losing the
# middle of the message.
CHUNK = 5500
MAX_CHUNKS = 10

# Lines worth surfacing first: p4a/buildozer say "Error:", "ERROR", "fatal:",
# python raises "Traceback", make says "Error 1", gradle says "FAILURE:".
_INTERESTING = re.compile(
    r"(error|Error|ERROR|fatal|Fatal|Traceback|Exception|Failed|FAILED|FAILURE"
    r"|not found|No such file|undefined reference|cannot find|refused)"
)

_TAIL_LINES = 160
_ERROR_CONTEXT = 4
_MAX_ERRORS = 40


def _escape(text: str) -> str:
    """Escape a message for a workflow command (percent, CR and LF)."""
    return (text.replace("%", "%25")
                .replace("\r", "%0D")
                .replace("\n", "%0A"))


def _chunks(title: str, lines: list[str]) -> list[str]:
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
    return commands[:MAX_CHUNKS]


def _error_block(lines: list[str]) -> list[str]:
    """The last few error lines with a little context around each."""
    hits = [i for i, line in enumerate(lines) if _INTERESTING.search(line)]
    if not hits:
        return []
    # Collapse adjacent hits so a traceback is printed once, not per line.
    groups: list[tuple[int, int]] = []
    for idx in hits[-_MAX_ERRORS:]:
        start = max(0, idx - _ERROR_CONTEXT)
        end = min(len(lines), idx + _ERROR_CONTEXT + 1)
        if groups and start <= groups[-1][1]:
            groups[-1] = (groups[-1][0], max(groups[-1][1], end))
        else:
            groups.append((start, end))
    out: list[str] = []
    for start, end in groups:
        out.extend(lines[start:end])
        out.append("--")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: report_build_log.py <log file>", file=sys.stderr)
        return 0
    path = Path(argv[1])
    if not path.is_file():
        print(f"::error title=buildozer log::no log file at {path}")
        return 0

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        print("::error title=buildozer log::the build produced no output")
        return 0

    commands = _chunks("buildozer errors", _error_block(lines))
    commands += _chunks("buildozer tail", lines[-_TAIL_LINES:])
    for command in commands:
        print(command)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## buildozer log (tail)\n\n```\n")
            handle.write("\n".join(lines[-_TAIL_LINES:]))
            handle.write("\n```\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
