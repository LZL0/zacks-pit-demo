"""The published API reference page renders the committed spec with a pinned renderer."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "site" / "index.html"
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"


def test_page_renders_the_committed_spec_with_a_pinned_renderer() -> None:
    html = PAGE.read_text()
    assert 'url: "./openapi.json"' in html
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert scripts, "no renderer script"
    for src in scripts:
        assert src.startswith("https://cdn.jsdelivr.net/npm/"), src
        assert re.search(r"@\d+\.\d+\.\d+/", src), f"renderer not pinned to an exact version: {src}"


def test_page_has_no_telemetry_or_hosted_ai() -> None:
    html = PAGE.read_text()
    for setting in ("telemetry: false", "agent: { disabled: true }", "mcp: { disabled: true }"):
        assert setting in html, setting
    assert "proxyUrl" not in html  # requests stay between the reader and the API


def test_pages_workflow_publishes_the_generated_spec() -> None:
    workflow = WORKFLOW.read_text()
    assert "cp openapi.json _site/" in workflow
    assert "cp site/index.html _site/" in workflow
    for line in workflow.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40} # v\d", line), f"action not pinned to a SHA: {line}"
