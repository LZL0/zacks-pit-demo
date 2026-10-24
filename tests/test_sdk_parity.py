"""One schema, three representations. Regenerate and fail on any drift.

``src/pitdemo/schemas.py`` -> ``sdk/schema.json`` -> Python models + Zod schemas.
Fix a failure with ``uv run demo sdk``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

import pytest

from pitdemo.sdkgen import PY_MODELS, SCHEMA_PATH, SDK, python_models_text, schema_text
from pitdemo.service import Service

sys.path.insert(0, str(SDK / "python"))
from pitdemo_sdk import models as sdk_models

TS = SDK / "ts"


def test_schema_json_matches_pydantic_models() -> None:
    assert SCHEMA_PATH.read_text() == schema_text(), "sdk/schema.json drifted: run `demo sdk`"


def test_python_sdk_models_match_schema() -> None:
    assert PY_MODELS.read_text() == python_models_text(), "Python SDK drifted: run `demo sdk`"


@pytest.mark.skipif(
    shutil.which("node") is None or not (TS / "node_modules").exists(),
    reason="TypeScript toolchain not installed (CI runs this check separately)",
)
def test_ts_zod_schemas_match_schema() -> None:
    result = subprocess.run(
        ["node", "scripts/gen.mjs", "--check"], cwd=TS, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_server_output_validates_against_sdk_models(service: Service) -> None:
    """Round trip: what the server sends parses in the generated SDK models."""
    r = service.get_consensus("NVDA", "FY2025", "2024-05-31")
    parsed = sdk_models.ConsensusResult.model_validate_json(r.model_dump_json())
    assert parsed.mean == r.mean
    assert parsed.as_of.tzinfo is not None  # explicit UTC on the wire
    e = service.explain("NVDA", "2024-06-14", "FY2025")
    sdk_models.AdjustmentExplanation.model_validate_json(e.model_dump_json())
    a = service.corporate_actions("GE")
    sdk_models.CorporateActionList.model_validate_json(a.model_dump_json())


def test_every_api_model_is_in_the_schema() -> None:
    import json

    all_defs = json.loads(SCHEMA_PATH.read_text())["$defs"]
    defs = set(all_defs)
    assert {
        "ConsensusResult",
        "ConsensusSeries",
        "CorporateActionList",
        "ErrorResponse",
        "AdjustmentExplanation",
        "RevisionSignal",
        "Universe",
        "ConsensusQuery",
    } <= defs
    # Object models must exist as classes; plain aliases (e.g. FixedPeriodParam, a
    # constrained string) may be inlined into the fields that use them.
    for name, spec in all_defs.items():
        if spec.get("type") == "object":
            assert hasattr(sdk_models, name), name
    ts = (TS / "src" / "schemas.ts").read_text()
    for name in defs:
        assert f"export const {name}Schema" in ts, name


# ------------------------------------------------------------ OpenAPI / MCP


def test_openapi_json_matches_the_code() -> None:
    from pitdemo.sdkgen import OPENAPI_PATH, openapi_text

    assert OPENAPI_PATH.read_text() == openapi_text(), "openapi.json drifted: run `demo sdk`"


def test_openapi_is_a_valid_dated_spec() -> None:
    import json

    from openapi_spec_validator import validate

    from pitdemo import API_VERSION
    from pitdemo.sdkgen import OPENAPI_PATH

    spec = json.loads(OPENAPI_PATH.read_text())
    validate(spec)
    assert spec["info"]["version"] == API_VERSION
    assert re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", API_VERSION)
    # Each endpoint documents exactly the status codes it can return.
    data = {"200", "400", "404"}
    expected = {
        "/consensus": data,
        "/consensus/series": data,
        "/corporate-actions": data,
        "/explain": data,
        "/revisions": data,
        "/universe": data,
        "/healthz": {"200"},
    }
    documented = {p: set(v["get"]["responses"]) for p, v in spec["paths"].items()}
    assert documented == expected


def test_openapi_and_sdk_share_one_schema() -> None:
    """Every OpenAPI component is a model in sdk/schema.json, i.e. one source of truth."""
    import json

    from pitdemo.sdkgen import OPENAPI_PATH

    components = set(json.loads(OPENAPI_PATH.read_text())["components"]["schemas"])
    defs = set(json.loads(SCHEMA_PATH.read_text())["$defs"])
    assert components <= defs, components - defs


def test_mcp_tool_definitions_match_the_code() -> None:
    import json

    from pitdemo.sdkgen import MCP_TOOLS_PATH, mcp_tools_text

    assert MCP_TOOLS_PATH.read_text() == mcp_tools_text(), "MCP tools drifted: run `demo sdk`"
    tools = {t["name"]: t for t in json.loads(MCP_TOOLS_PATH.read_text())["tools"]}
    assert sorted(tools) == ["explain_adjustment", "get_consensus", "list_corporate_actions"]
    assert tools["get_consensus"]["outputSchema"]["title"] == "ConsensusResult"
