"""Ask Claude Code a question with the pit-consensus MCP server and show the run as it streams.

This is a live run, not a replay: it shells out to `claude -p` (headless Claude Code)
with this repo's .mcp.json, then prints the MCP tool calls, a one-line summary of each
tool result, and Claude's answer. Used by demo.tape to make docs/demo.gif.

    uv run python scripts/record/ask.py "What was NVDA's FY2025 EPS consensus on 2024-05-31?"
    uv run python scripts/record/ask.py --continue "How was the split handled?"
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ["get_consensus", "explain_adjustment", "list_corporate_actions"]
ALLOWED = ",".join(f"mcp__pit-consensus__{t}" for t in TOOLS)
STYLE = (
    "You are being screen-recorded for a 60-second demo. Keep each answer under 70 words, "
    "plain sentences, no tables or headings."
)

DIM, BOLD, CYAN, GREEN, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[0m"
WIDTH = 92


def say(text: str = "", style: str = "", indent: str = "  ", hang: str = "") -> None:
    """Print wrapped text; continuation lines get ``hang`` extra indentation."""
    for para in text.splitlines() or [""]:
        lines = textwrap.wrap(
            para, WIDTH, initial_indent=indent, subsequent_indent=indent + hang
        ) or [indent]
        for line in lines:
            print(f"{style}{line}{RESET if style else ''}", flush=True)


def summarize(name: str, payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)[:120]
    if name == "get_consensus":
        return (
            f"mean {payload['mean']:.3f} · median {payload['median']:.3f} · "
            f"{payload['count']} estimates · {payload['excluded_total']} excluded · "
            f"{payload['target_basis']}"
        )
    if name == "explain_adjustment":
        first = payload["narrative"][0] if payload.get("narrative") else ""
        return first.split(". Source:")[0]
    if name == "list_corporate_actions":
        acts = payload.get("actions", [])
        return ", ".join(f"{a['action_type']} {a['ex_date']}" for a in acts)
    return ""


def tool_result_payload(block: dict[str, Any]) -> Any:
    content = block.get("content")
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    try:
        return json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        return content


def main(argv: list[str]) -> int:
    resume = argv[:1] == ["--continue"]
    question = " ".join(argv[1:] if resume else argv)
    print(f"{BOLD}❯ {question}{RESET}\n", flush=True)
    cmd = [
        "claude",
        "-p",
        question,
        "--mcp-config",
        str(ROOT / ".mcp.json"),
        "--strict-mcp-config",
        "--allowedTools",
        ALLOWED,
        "--append-system-prompt",
        STYLE,
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if resume:
        cmd.insert(1, "--continue")
    names: dict[str, str] = {}
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = msg.get("type")
        if kind == "assistant":
            for block in msg["message"]["content"]:
                if block["type"] == "tool_use":
                    name = block["name"].removeprefix("mcp__pit-consensus__")
                    names[block["id"]] = name
                    if block["name"] == "Skill":
                        say(f"⏺ Skill: {block['input'].get('skill')}", DIM)
                    elif name in TOOLS:
                        args = ", ".join(f"{k}={v!r}" for k, v in block["input"].items())
                        say(f"⏺ {name}({args})", CYAN, hang="    ")
                elif block["type"] == "text" and block["text"].strip():
                    print()
                    say(block["text"].replace("**", ""))
        elif kind == "user":
            for block in msg["message"].get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    name = names.get(block["tool_use_id"], "")
                    if name in TOOLS:
                        say(f"⎿ {summarize(name, tool_result_payload(block))}", GREEN, "    ", "  ")
        elif kind == "result":
            secs = msg.get("duration_ms", 0) / 1000
            note = f"({secs:.0f}s · live Claude Code run · estimates are synthetic)"
            print(f"\n{DIM}  {note}{RESET}\n")
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
