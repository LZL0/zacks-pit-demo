"""The headline chart: NVDA FY2025 consensus around the June 2024 10-for-1 split."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from pitdemo.calendar import trading_days
from pitdemo.store import Store
from pitdemo.transforms.consensus import (
    as_of_instant,
    compute_consensus,
    consensus_entry_date,
    consensus_naive,
    load_inputs,
)

# Reference categorical palette (validated: CVD and normal-vision separation pass).
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e6e5e1"
CORRECT = "#2a78d6"
NAIVE = "#eb6834"
SHORTCUT = "#1baf7a"

NVDA = "SEC-0001"
EX_DATE = dt.date(2024, 6, 10)
ANNOUNCED = dt.date(2024, 5, 22)


@dataclass(frozen=True)
class ChartData:
    days: list[dt.date]
    lines: dict[str, list[float | None]]  # 'correct' | 'naive' | 'shortcut'


def nvda_series(
    store: Store, start: dt.date = dt.date(2024, 5, 1), end: dt.date = dt.date(2024, 7, 31)
) -> ChartData:
    inputs = load_inputs(store, NVDA)
    days = trading_days(start, end)
    lines: dict[str, list[float | None]] = {"correct": [], "naive": [], "shortcut": []}
    for d in days:
        t = as_of_instant(d)
        lines["correct"].append(
            compute_consensus(inputs, "FY2025", t, "adjusted", max_items=0).mean
        )
        lines["naive"].append(consensus_naive(inputs, "FY2025", t))
        lines["shortcut"].append(consensus_entry_date(inputs, "FY2025", t))
    return ChartData(days, lines)


def render_nvda_chart(store: Store, out: Path) -> Path:
    plt.switch_backend("Agg")
    data = nvda_series(store)
    days, s = data.days, data.lines

    plt.rcParams.update(
        {
            "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 11,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT_2,
            "xtick.color": TEXT_2,
            "ytick.color": TEXT_2,
            "text.parse_math": False,  # dollar signs are currency here
        }
    )
    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    lines = [
        ("naive", NAIVE, "-", "Naive: average of raw published values"),
        ("shortcut", SHORTCUT, (0, (4, 2)), "Shortcut: adjust by entry date"),
        ("correct", CORRECT, "-", "This pipeline: per-estimate share basis"),
    ]
    for key, color, style, label in lines:
        ax.plot(
            days,
            s[key],
            color=color,
            lw=2.2 if key == "correct" else 2,
            ls=style,
            solid_capstyle="round",
            solid_joinstyle="round",
            label=label,
            zorder=4 if key == "correct" else 3,
        )

    ax.axvline(EX_DATE, color=TEXT_2, lw=1, zorder=2)
    ax.text(
        EX_DATE,
        29.2,
        "  ex-date 2024-06-10\n  10-for-1 split",
        color=TEXT,
        fontsize=10,
        va="top",
        ha="left",
    )
    ax.axvline(ANNOUNCED, color=GRID, lw=1, zorder=1)
    ax.text(ANNOUNCED, 29.2, "split announced  ", color=TEXT_2, fontsize=10, va="top", ha="right")

    # Direct label at the right edge, in text ink. Naive has converged onto the same
    # value by then (every broker has restated), so one label serves both.
    last = days[-1]
    c_end = s["correct"][-1]
    assert isinstance(c_end, float)
    ax.annotate(
        f"${c_end:.2f}",
        (last, c_end),
        xytext=(8, 0),
        textcoords="offset points",
        va="center",
        fontsize=10,
        color=TEXT,
    )

    arrow = {"arrowstyle": "-", "color": TEXT_2, "lw": 0.8, "shrinkA": 2, "shrinkB": 3}
    peak_i = max(range(len(days)), key=lambda i: s["shortcut"][i] or 0.0)
    peak = s["shortcut"][peak_i]
    assert isinstance(peak, float)
    ax.annotate(
        f"Shortcut spikes to ${peak:.2f}: laggards' pre-split\n"
        "numbers are not divided, early restaters' are divided twice",
        (days[peak_i], peak),
        xytext=(dt.date(2024, 5, 3), 11.5),
        textcoords="data",
        fontsize=10,
        color=TEXT,
        arrowprops=arrow,
    )
    i0 = days.index(dt.date(2024, 5, 31))
    n0, n1 = s["naive"][i0], s["naive"][-1]
    assert isinstance(n0, float)
    assert isinstance(n1, float)
    drop_day = dt.date(2024, 6, 20)
    ax.annotate(
        f"Naive falls from ${n0:.2f} to ${n1:.2f}:\na fake {1 - n1 / n0:.0%} collapse",
        (drop_day, s["naive"][days.index(drop_day)]),
        xytext=(dt.date(2024, 7, 1), 13.5),
        textcoords="data",
        fontsize=10,
        color=TEXT,
        arrowprops=arrow,
    )
    ax.annotate(
        "Correct: flat through the split",
        (dt.date(2024, 7, 8), s["correct"][days.index(dt.date(2024, 7, 8))]),
        xytext=(dt.date(2024, 7, 1), 6.0),
        textcoords="data",
        fontsize=10,
        color=TEXT,
        arrowprops=arrow,
    )

    ax.set_ylim(0, 30)
    ax.set_xlim(days[0], last + dt.timedelta(days=12))
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:.0f}")
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_minor_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.grid(axis="y", color=GRID, lw=1)
    ax.tick_params(length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.set_ylabel("FY2025 EPS consensus (USD per share)")

    fig.text(
        0.06,
        0.95,
        "NVIDIA FY2025 EPS consensus around the 10-for-1 split",
        fontsize=15,
        weight="bold",
        color=TEXT,
    )
    fig.text(
        0.06,
        0.905,
        "Same estimates, three ways to combine them. Brokers restated on their own "
        "schedule, from 3 trading days before the ex-date to 10 after.",
        fontsize=11,
        color=TEXT_2,
    )
    leg = ax.legend(loc="upper right", bbox_to_anchor=(0.995, 0.86), frameon=False, fontsize=10)
    for t in leg.get_texts():
        t.set_color(TEXT)
    fig.text(
        0.06,
        0.025,
        "Analyst estimates are SYNTHETIC (seed 42), calibrated to NVIDIA's real FY2025 "
        "diluted EPS (SEC EDGAR). Split dates from NVIDIA's 8-K. Values on the post-split "
        "share basis.",
        fontsize=9,
        color=TEXT_2,
    )
    fig.subplots_adjust(left=0.08, right=0.97, top=0.86, bottom=0.11)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return out
