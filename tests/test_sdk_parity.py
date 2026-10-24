"""One schema, three representations. Regenerate and fail on any drift.

``src/pitdemo/schemas.py`` -> ``sdk/schema.json`` -> Python models + Zod schemas.
Fix a failure with ``uv run demo sdk``.
"""

from __future__ import annotations

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

    defs = set(json.loads(SCHEMA_PATH.read_text())["$defs"])
    assert {
        "ConsensusResult",
        "ConsensusSeries",
        "CorporateActionList",
        "ErrorResponse",
        "AdjustmentExplanation",
        "RevisionSignal",
    } <= defs
    for name in defs:
        assert hasattr(sdk_models, name), name
    ts = (TS / "src" / "schemas.ts").read_text()
    for name in defs:
        assert f"export const {name}Schema" in ts, name
