"""The published API reference: pinned, integrity-checked renderer; main-only deploys."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "site" / "index.html"
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"


class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.external: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "script" and a.get("src"):
            self.external.append(a)


def _external_scripts() -> list[dict[str, str | None]]:
    parser = _Scripts()
    parser.feed(PAGE.read_text())
    return parser.external


def test_every_external_script_is_pinned_and_integrity_checked() -> None:
    scripts = _external_scripts()
    assert scripts, "no renderer script"
    for s in scripts:
        src = s["src"] or ""
        assert src.startswith("https://cdn.jsdelivr.net/npm/"), src
        assert re.search(r"@\d+\.\d+\.\d+/", src), f"not pinned to an exact version: {src}"
        assert re.fullmatch(r"sha384-[A-Za-z0-9+/]{64}", s.get("integrity") or ""), src
        assert s.get("crossorigin") == "anonymous", src


def test_page_renders_the_committed_spec_without_telemetry_or_hosted_ai() -> None:
    html = PAGE.read_text()
    assert 'url: "./openapi.json"' in html
    for setting in ("telemetry: false", "agent: { disabled: true }", "mcp: { disabled: true }"):
        assert setting in html, setting
    assert "proxyUrl" not in html  # requests stay between the reader and the API


def test_pages_workflow_publishes_only_main_with_scoped_permissions() -> None:
    text = WORKFLOW.read_text()
    wf = yaml.safe_load(text)
    assert wf["permissions"] == {"contents": "read"}
    jobs = wf["jobs"]
    for name in ("build", "deploy"):
        assert jobs[name]["if"] == "github.ref == 'refs/heads/main'", name
    assert jobs["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert "permissions" not in jobs["build"]
    assert "cp openapi.json _site/" in text
    for line in text.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40} # v\d", line), f"action not pinned to a SHA: {line}"
