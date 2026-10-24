"""SDK generation from one schema.

``schemas.py`` (Pydantic) -> ``sdk/schema.json`` (JSON Schema) -> generated models:

* Python: ``sdk/python/pitdemo_sdk/models.py`` via datamodel-code-generator
* TypeScript: ``sdk/ts/src/schemas.ts`` via json-schema-to-zod

``tests/test_sdk_parity.py`` and CI regenerate all three and fail on any diff.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pydantic.json_schema import models_json_schema

from pitdemo.schemas import API_MODELS

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "sdk"
SCHEMA_PATH = SDK / "schema.json"
PY_MODELS = SDK / "python" / "pitdemo_sdk" / "models.py"


def export_schema() -> dict[str, Any]:
    _, doc = models_json_schema(
        [(m, "serialization") for m in API_MODELS],
        title="pitdemo API",
        ref_template="#/$defs/{model}",
    )
    doc["description"] = (
        "Boundary types for the point-in-time consensus demo. Generated from "
        "src/pitdemo/schemas.py; do not edit by hand."
    )
    return doc


def schema_text() -> str:
    return json.dumps(export_schema(), indent=2, sort_keys=True) + "\n"


def python_models_text(schema_path: Path = SCHEMA_PATH) -> str:
    exe = shutil.which("datamodel-codegen")
    if exe is None:
        raise RuntimeError("datamodel-codegen not found; run `uv sync` (dev dependencies)")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "models.py"
        subprocess.run(
            [
                exe,
                "--input",
                str(schema_path),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(out),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.12",
                "--use-annotated",
                "--use-standard-collections",
                "--use-union-operator",
                "--field-constraints",
                "--use-double-quotes",
                "--disable-timestamp",
                "--custom-file-header",
                "# Generated from sdk/schema.json by `demo sdk`. Do not edit by hand.",
            ],
            check=True,
            capture_output=True,
        )
        return out.read_text()


def write_all() -> list[Path]:
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(schema_text())
    PY_MODELS.parent.mkdir(parents=True, exist_ok=True)
    PY_MODELS.write_text(python_models_text())
    return [SCHEMA_PATH, PY_MODELS]
