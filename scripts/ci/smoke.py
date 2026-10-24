"""End-to-end smoke test of the real wiring, as a user would run it.

1. Builds the DuckDB file (``demo build``).
2. Starts the HTTP server (``demo serve``) as a subprocess and waits for /healthz.
3. Runs the Python SDK and the TypeScript SDK (installed and built if needed) against it.
4. Starts the MCP server over stdio (``demo mcp``) and calls a tool.

Unit tests cover each piece in-process; this checks that the processes, ports,
entry points and serialization line up. Exits non-zero on the first failure.

    uv run python scripts/ci/smoke.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("SMOKE_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"


def step(name: str) -> None:
    print(f"\n== {name}", flush=True)


def require(ok: bool, message: str) -> None:
    """Explicit check (not ``assert``, which ``python -O`` strips)."""
    if not ok:
        raise SystemExit(f"FAIL {message}")


def wait_for_server(proc: subprocess.Popen[bytes], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"server exited early with code {proc.returncode}")
        try:
            if httpx.get(f"{URL}/healthz", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise SystemExit(f"server did not become healthy within {timeout:.0f}s")


def python_sdk() -> None:
    sys.path.insert(0, str(ROOT / "sdk" / "python"))
    from pitdemo_sdk import PitApiError, PitClient

    with PitClient(URL) as c:
        r = c.consensus("NVDA", "FY2025", "2024-05-31")
        require(r.mean is not None and 1 < r.mean < 5, f"post-split mean, got {r.mean}")
        require(r.count >= 3 and not r.insufficient, f"at least 3 estimates, got {r.count}")
        require(r.as_of.tzinfo is not None, "as_of is timezone-aware (explicit UTC)")
        frame = c.consensus_frame("NVDA", "FY2025", "2024-06-03", "2024-06-14")
        require(frame.height == 10, f"10 trading days in the frame, got {frame.height}")
        error: PitApiError | None = None
        try:
            c.consensus("SIVB", "FY2023", "2023-06-01")
        except PitApiError as exc:
            error = exc
        require(
            error is not None
            and error.status == 404
            and error.error is not None
            and error.error.error.value == "NotFound",
            f"typed NotFound for SIVB after its ticker was retired, got {error!r}",
        )
        print(f"ok   Python SDK: NVDA FY2025 {r.mean:.3f} from {r.count} estimates")


def ts_sdk() -> None:
    node, npm = shutil.which("node"), shutil.which("npm")
    if node is None or npm is None:
        raise SystemExit("node/npm not found; the TypeScript SDK check needs Node.js")
    ts = ROOT / "sdk" / "ts"
    # Works from a clean checkout: install and build the SDK if needed.
    if not (ts / "node_modules").exists():
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=ts, check=True)
    subprocess.run([npm, "run", "-s", "build"], cwd=ts, check=True)
    subprocess.run(
        [node, "scripts/smoke.mjs"], cwd=ts, env={**os.environ, "PITDEMO_URL": URL}, check=True
    )


async def mcp_stdio() -> None:
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(
        command="uv", args=["--directory", str(ROOT), "run", "demo", "mcp"]
    )
    async with Client(params) as client:
        tools = sorted(t.name for t in (await client.list_tools()).tools)
        expected = ["explain_adjustment", "get_consensus", "list_corporate_actions"]
        require(tools == expected, f"MCP tools {tools}")
        r = await client.call_tool(
            "get_consensus", {"ticker": "NVDA", "fiscal_period": "FY2025", "as_of": "2024-05-31"}
        )
        require(not r.is_error and r.structured_content is not None, f"get_consensus: {r}")
        assert r.structured_content is not None  # narrowing for mypy; checked above
        mean = r.structured_content["mean"]
        e = await client.call_tool(
            "get_consensus", {"ticker": "SIVB", "fiscal_period": "FY2023", "as_of": "2023-06-01"}
        )
        require(e.is_error and "NotFound" in str(e.content), f"typed MCP error, got {e}")
        print(f"ok   MCP stdio: {len(tools)} tools, get_consensus mean {mean:.3f}, typed errors")


def main() -> None:
    step("build database")
    subprocess.run(["uv", "run", "demo", "build"], cwd=ROOT, check=True)

    step(f"start HTTP server on :{PORT}")
    server = subprocess.Popen(
        ["uv", "run", "demo", "serve", "--port", str(PORT)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_for_server(server)
        step("Python SDK against the server")
        python_sdk()
        step("TypeScript SDK against the server")
        ts_sdk()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    step("MCP over stdio")
    asyncio.run(mcp_stdio())
    print("\nsmoke test passed")


if __name__ == "__main__":
    main()
