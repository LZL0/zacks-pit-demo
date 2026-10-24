"""Capture a live two-question Claude Code session as timestamped output lines.

Writes an asciinema-v2-style file (header line, then ``[seconds, "o", text]`` events)
that render.py turns into docs/demo.mp4 and docs/demo.gif.

    uv run python scripts/record/capture.py docs/demo.cast
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUESTIONS = [
    "What was NVDA's FY2025 EPS consensus on 2024-05-31, split-adjusted?",
    "How was the split handled for that number? Use explain_adjustment.",
]


def main(out: Path) -> None:
    session = str(uuid.uuid4())
    events: list[list[object]] = []
    t0 = time.monotonic()
    for i, question in enumerate(QUESTIONS):
        flag = "--session" if i == 0 else "--resume"
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "ask.py"), flag, session, question],
            stdout=subprocess.PIPE,
            text=True,
            env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            events.append([round(time.monotonic() - t0, 3), "o", line])
            sys.stdout.write(line)
            sys.stdout.flush()
        if proc.wait() != 0:
            raise SystemExit(f"question {i + 1} failed")
    header = {"version": 2, "width": 96, "height": 34, "title": "pit-consensus MCP demo"}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        fh.write(json.dumps(header) + "\n")
        for e in events:
            fh.write(json.dumps(e) + "\n")
    print(f"wrote {out} ({len(events)} lines, {events[-1][0]:.0f}s)", file=sys.stderr)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/demo.cast"))
