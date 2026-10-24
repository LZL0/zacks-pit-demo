"""Render a captured session (capture.py) to docs/demo.mp4 and docs/demo.gif.

Draws a terminal window with Pillow and encodes with ffmpeg. Output appears with
its real ordering; waits for the model longer than ``MAX_GAP`` are shortened (a
spinner shows during them), which the caption says.

    uv run python scripts/record/render.py docs/demo.cast docs/demo.mp4 docs/demo.gif
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 760
FPS = 12
PAD_X, TOP, LINE_H = 30, 64, 24
FOOTER_H = 40
ROWS = (H - TOP - FOOTER_H - 12) // LINE_H
MAX_GAP = 1.4  # seconds; longer waits are shortened
TYPE_CPS = 38  # typing speed for the question lines
HOLD_END = 4.0
READ_HOLD = 7.0  # pause after each answer so it can be read

BG, CHROME, TEXT = (24, 24, 27), (39, 39, 43), (228, 228, 231)
DIM, CYAN, GREEN, CAPTION = (130, 130, 140), (103, 183, 230), (140, 205, 130), (160, 160, 170)
FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
SPINNER = "◐◓◑◒"
# Menlo lacks the glyphs Claude Code uses for tool calls; draw close equivalents.
GLYPHS = str.maketrans({"⏺": "●", "⎿": "└"})
ANSI = re.compile(r"\x1b\[(\d+)m")


@dataclass
class Span:
    text: str
    color: tuple[int, int, int]
    bold: bool


def parse_ansi(line: str) -> list[Span]:
    line = line.translate(GLYPHS)
    spans: list[Span] = []
    color, bold = TEXT, False
    pos = 0
    for m in ANSI.finditer(line):
        if m.start() > pos:
            spans.append(Span(line[pos : m.start()], color, bold))
        code = int(m.group(1))
        if code == 0:
            color, bold = TEXT, False
        elif code == 1:
            bold = True
        elif code == 2:
            color = DIM
        elif code == 36:
            color = CYAN
        elif code == 32:
            color = GREEN
        pos = m.end()
    if pos < len(line):
        spans.append(Span(line[pos:], color, bold))
    return spans


def load_events(cast: Path) -> list[tuple[float, str]]:
    lines = cast.read_text().splitlines()[1:]
    return [(float(e[0]), str(e[2]).rstrip("\n")) for e in map(json.loads, lines)]


Timed = list[tuple[float, str]]
Spans = list[tuple[float, float]]


def timeline(events: Timed) -> tuple[Timed, Spans]:
    """Re-time events: shorten long gaps, add typing for prompt lines.

    Returns (line_events, waits) where waits are (start, end) spans that get a spinner.
    """
    out: list[tuple[float, str]] = []
    waits: list[tuple[float, float]] = []
    t = 0.6
    prev_real: float | None = None
    for real, line in events:
        if prev_real is not None:
            gap = real - prev_real
            if gap > MAX_GAP:
                waits.append((t, t + MAX_GAP))
                t += MAX_GAP
            else:
                t += gap
        prev_real = real
        plain = ANSI.sub("", line)
        if plain.startswith("❯ "):
            t += 0.8
            n = len(plain)
            for i in range(3, n + 1):
                out.append((t, "\x1b[1m" + plain[:i] + "\x1b[0m"))
                t += 1 / TYPE_CPS
            out.append((t, "\x1b[1m" + plain + "\x1b[0m"))
            t += 0.5
            waits.append((t, t + 0.6))
            t += 0.6
        else:
            out.append((t, line))
            t += 0.05
            if "live Claude Code run" in plain:
                t += READ_HOLD
    return out, waits


def build_states(line_events: list[tuple[float, str]]) -> list[tuple[float, list[str]]]:
    """Screen contents after each event; typing frames replace the current line."""
    states: list[tuple[float, list[str]]] = []
    screen: list[str] = []
    typing = False
    for t, line in line_events:
        plain = ANSI.sub("", line)
        is_prompt = plain.startswith("❯ ")
        if is_prompt and typing:
            screen[-1] = line
        else:
            screen.append(line)
        typing = is_prompt
        states.append((t, list(screen)))
    return states


def draw_frame(
    lines: list[str], spinner: str | None, fonts: dict[bool, ImageFont.FreeTypeFont]
) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 40], fill=CHROME)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([18 + i * 22, 14, 30 + i * 22, 26], fill=c)
    title = "claude -p  ·  pit-consensus MCP server  ·  live run"
    tw = d.textlength(title, font=fonts[False])
    d.text(((W - tw) / 2, 11), title, fill=DIM, font=fonts[False])
    visible = lines[-ROWS:] if spinner is None else lines[-(ROWS - 1) :]
    y = TOP
    for line in visible:
        x: float = PAD_X
        for span in parse_ansi(line):
            d.text((x, y), span.text, fill=span.color, font=fonts[span.bold])
            x += d.textlength(span.text, font=fonts[span.bold])
        y += LINE_H
    if spinner is not None:
        d.text((PAD_X, y), f"  {spinner} Claude is working…", fill=DIM, font=fonts[False])
    d.rectangle([0, H - FOOTER_H, W, H], fill=CHROME)
    caption = (
        "Real Claude Code calling this repo's MCP server. Waits for the model are shortened. "
        "Analyst estimates are synthetic."
    )
    cw = d.textlength(caption, font=fonts[False])
    d.text(((W - cw) / 2, H - FOOTER_H + 11), caption, fill=CAPTION, font=fonts[False])
    return img


def render(cast: Path, mp4: Path, gif: Path | None) -> float:
    fonts = {
        False: ImageFont.truetype(FONT_PATH, 17, index=0),
        True: ImageFont.truetype(FONT_PATH, 17, index=1),
    }
    line_events, waits = timeline(load_events(cast))
    states = build_states(line_events)
    duration = states[-1][0] + HOLD_END
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg not found")
    mp4.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{W}x{H}",
            "-r",
            str(FPS),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        stdin=subprocess.PIPE,
    )
    assert proc.stdin is not None
    idx = 0
    n_frames = int(duration * FPS)
    cache: dict[tuple[int, str | None], bytes] = {}
    for f in range(n_frames):
        t = f / FPS
        while idx + 1 < len(states) and states[idx + 1][0] <= t:
            idx += 1
        lines = states[idx][1] if states[0][0] <= t else []
        spin = next((s for s in waits if s[0] <= t < s[1]), None)
        spinner = SPINNER[(f // 3) % len(SPINNER)] if spin else None
        key = (idx if states[0][0] <= t else -1, spinner)
        if key not in cache:
            cache.clear()
            cache[key] = draw_frame(lines, spinner, fonts).tobytes()
        proc.stdin.write(cache[key])
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg failed")
    if gif is not None:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mp4),
                "-vf",
                "fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];"
                "[b][p]paletteuse=dither=none",
                str(gif),
            ],
            check=True,
        )
    return duration


if __name__ == "__main__":
    args = [Path(a) for a in sys.argv[1:]] or [
        Path("docs/demo.cast"),
        Path("docs/demo.mp4"),
        Path("docs/demo.gif"),
    ]
    secs = render(args[0], args[1], args[2] if len(args) > 2 else None)
    print(f"rendered {secs:.1f}s")
