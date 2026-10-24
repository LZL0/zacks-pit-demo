"""The weekly source check flags upstream edits to history, not new filings."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType

from pitdemo.ingest.edgar import load_raw

ROOT = Path(__file__).resolve().parents[1]


def _verify() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "verify_sources", ROOT / "scripts" / "ci" / "verify_sources.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_filings_are_notes_not_problems() -> None:
    verify = _verify()
    old = load_raw(ROOT / "data" / "raw", 1045810)
    new = copy.deepcopy(old)
    new["filings"].append(
        {
            "accession": "0001045810-99-999999",
            "form": "8-K",
            "filing_date": "2027-01-01",
            "acceptance": "2027-01-01T21:00:00.000Z",
            "items": "2.02",
        }
    )
    problems, notes = verify.compare_history(old, new)
    assert problems == []
    assert notes == ["NVIDIA CORP: 0 new EPS facts, 1 new filings since commit"]


def test_edited_history_is_a_problem() -> None:
    verify = _verify()
    old = load_raw(ROOT / "data" / "raw", 1045810)
    new = copy.deepcopy(old)
    new["eps_diluted"][0]["val"] = 999.0  # SEC silently changed a past value
    new["filings"] = new["filings"][1:]  # and a filing vanished
    problems, _ = verify.compare_history(old, new)
    assert len(problems) == 2
    assert "EPS fact changed or removed upstream" in problems[0]
    assert "filing changed or removed upstream" in problems[1]
