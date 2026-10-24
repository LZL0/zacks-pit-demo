"""The figures quoted in the README, methodology and agent skill match the code.

They are written by hand, so this recomputes each one and fails with the expected
text when the prose goes stale (e.g. after regenerating the synthetic data).
"""

from __future__ import annotations

import datetime as dt
import itertools
import re
from pathlib import Path

import polars as pl
import pytest

from pitdemo.chart import nvda_series
from pitdemo.pipeline import ESTIMATES_PATH, RawData
from pitdemo.store import Store
from pitdemo.transforms.consensus import (
    as_of_instant,
    compute_consensus,
    consensus_entry_date,
    consensus_naive,
    load_inputs,
)
from pitdemo.transforms.factors import cumulative_factor

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
METHODOLOGY = ROOT / "docs" / "methodology.md"
SKILL = ROOT / ".claude" / "skills" / "query-pit-consensus" / "SKILL.md"
HEADLINE = dt.date(2024, 5, 31)
TABLE_DATES = (dt.date(2024, 5, 31), dt.date(2024, 6, 14), dt.date(2024, 7, 31))


def assert_quoted(path: Path, expected: str) -> None:
    text = path.read_text()
    assert expected in text, (
        f"{path.relative_to(ROOT)} is stale: expected to find {expected!r}. "
        "Update the prose to match the current data."
    )


@pytest.fixture(scope="module")
def facts(full_store: Store) -> dict[str, object]:
    store = full_store
    inputs = load_inputs(store, "SEC-0001")
    t = as_of_instant(HEADLINE)
    adj = compute_consensus(inputs, "FY2025", t, "adjusted")
    unadj = compute_consensus(inputs, "FY2025", t, "unadjusted")
    rows = {
        d: (
            compute_consensus(inputs, "FY2025", as_of_instant(d), "adjusted").mean,
            consensus_naive(inputs, "FY2025", as_of_instant(d)),
            consensus_entry_date(inputs, "FY2025", as_of_instant(d)),
        )
        for d in TABLE_DATES
    }
    chart = nvda_series(store)
    shortcut = [v for v in chart.lines["shortcut"] if v is not None]
    # "Flat through the split": the correct line from June on (May still has the
    # step up at NVIDIA's 2024-05-22 earnings release).
    correct = [
        v
        for d, v in zip(chart.days, chart.lines["correct"], strict=True)
        if v is not None and d >= dt.date(2024, 6, 1)
    ]
    return {
        "adj": adj,
        "unadj": unadj,
        "rows": rows,
        "shortcut_min": min(shortcut),
        "shortcut_max": max(shortcut),
        "correct_mid": (min(correct) + max(correct)) / 2,
    }


def test_readme_headline_answer(facts: dict[str, object]) -> None:
    adj, unadj = facts["adj"], facts["unadj"]
    assert_quoted(
        README,
        f"gets **${adj.mean:.3f}**: the mean of {adj.count} estimates",  # type: ignore[attr-defined]
    )
    assert_quoted(README, f"the same consensus is ${unadj.mean:.2f}.")  # type: ignore[attr-defined]
    excluded = adj.excluded_total  # type: ignore[attr-defined]
    if excluded == 1:
        assert_quoted(README, "One more estimate was excluded")
    else:
        assert "One more estimate was excluded" not in README.read_text()


def test_readme_chart_figures(facts: dict[str, object]) -> None:
    lo, hi = facts["shortcut_min"], facts["shortcut_max"]
    assert_quoted(README, f"swings between ${lo:.2f} and ${hi:.2f}")
    # "stays flat at about $X": X within 3% of the correct line's level.
    m = re.search(r"stays flat at about \$(\d+\.\d+)", README.read_text())
    assert m is not None
    assert abs(float(m.group(1)) / facts["correct_mid"] - 1) < 0.03  # type: ignore[operator]
    rows = facts["rows"]
    start, end = rows[TABLE_DATES[0]][1], rows[TABLE_DATES[-1]][1]  # type: ignore[index]
    assert_quoted(README, f"falls {1 - end / start:.0%}")


def test_methodology_table(facts: dict[str, object]) -> None:
    for d, (mean, naive, entry) in facts["rows"].items():  # type: ignore[attr-defined]
        assert_quoted(METHODOLOGY, f"| {d} | ${mean:.3f} | ${naive:.2f} | ${entry:.2f} |")


def test_methodology_dataset_size() -> None:
    est = pl.read_parquet(ESTIMATES_PATH)
    assert_quoted(METHODOLOGY, f"({est.height:,} rows, {est['broker_id'].n_unique()} brokers)")


def test_restatement_pair_count(raw: RawData) -> None:
    pairs = 0
    for sid, actuals in raw.actuals.items():
        factors = raw.factors(sid)
        for _, g in actuals.group_by(["security_id", "fiscal_period"]):
            vals = g.sort("filed_at").unique(subset=["value", "filed_at"], maintain_order=True)
            seq = vals.select("value", "filed_at").rows()
            for (_, t0), (_, t1) in itertools.pairwise(seq):
                if cumulative_factor(factors, t0.date(), t1.date()) != 1.0:
                    pairs += 1
    assert_quoted(METHODOLOGY, f"For all {pairs} pairs of EDGAR filings with a split between them")
    assert_quoted(README, f"for all {pairs} EDGAR restatement pairs")


def test_skill_example_answer(facts: dict[str, object]) -> None:
    adj, unadj = facts["adj"], facts["unadj"]
    assert_quoted(SKILL, f"USD {adj.mean:.2f} (mean of {adj.count} estimates")  # type: ignore[attr-defined]
    assert_quoted(SKILL, f"it was USD {unadj.mean:.2f}.")  # type: ignore[attr-defined]
    assert re.search(r"\$\d", SKILL.read_text()) is None, (
        "SKILL.md must not contain '$' followed by a digit: skills substitute arguments there"
    )
