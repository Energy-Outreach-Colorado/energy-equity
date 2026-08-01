"""Affordability-gap sizing: dollars needed to bring households to a burden threshold.

For each household the gap at threshold `t` is::

    gap = max(0, annual_energy_cost_adj - t * income_adjusted)

i.e. the annual dollar reduction in energy cost that would bring the household's energy
burden down to `t`. Weighted totals of this quantity size a percentage-of-income payment
plan or bill-assistance program: they answer "what would it cost to eliminate burden
above `t` in this service territory".

Gaps are defined only for households with positive income and a non-missing energy cost;
households with non-positive income are excluded regardless of `nonpos_income_rule`
(no meaningful payment target exists at zero income), matching the population whose
burden the thresholds describe. Because the gap is denominated in dollars of
self-reported cost, estimates inherit the level bias EIA-861 calibration corrects —
run calibration when gap totals will be published.

Margins of error use the standard SDR replicate machinery: the gap is a fixed
per-household dollar amount, so each replicate estimate is a re-weighted sum.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..weights.sdr import sdr_moe_scalar
from .scenarios import _default_populations, _segments_present
from .summaries import safe_div


def compute_affordability_gap(
    df: pd.DataFrame,
    *,
    threshold: float,
    cost_col: str = "annual_energy_cost_adj",
    income_col: str = "income_adjusted",
) -> pd.Series:
    """Per-household annual gap dollars at `threshold`; NaN where undefined.

    Undefined (NaN) when income is missing or non-positive, or when the energy cost is
    missing. Zero for households at or below the threshold.
    """
    cost = pd.to_numeric(df[cost_col], errors="coerce").astype(float)
    income = pd.to_numeric(df[income_col], errors="coerce").astype(float)
    gap = (cost - float(threshold) * income).clip(lower=0.0)
    return gap.where((income > 0) & cost.notna(), np.nan)


def _replicate_segment_weights(
    df: pd.DataFrame,
    segment_weight: pd.Series,
    replicate_weight_cols: Sequence[str],
    point_weight_col: str,
) -> np.ndarray | None:
    """Replicate analogs of a segment weight: WGTPr scaled by segment_weight / WGTP.

    Reproduces the service-share (and urban/rural) factors baked into the segment
    weight for every replicate column. Returns None when no replicate columns exist.
    """
    cols = [c for c in replicate_weight_cols if c in df.columns]
    if not cols:
        return None
    point = pd.to_numeric(df[point_weight_col], errors="coerce").astype(float)
    scale = (segment_weight / point).where(point > 0, 0.0).to_numpy(dtype=float)
    reps = df[cols].to_numpy(dtype=float)
    return reps * scale[:, np.newaxis]


def build_affordability_gap_summary(
    df: pd.DataFrame,
    *,
    thresholds: Sequence[float],
    populations: Mapping[str, Callable[[pd.DataFrame], pd.Series]] | None = None,
    replicate_weight_cols: Sequence[str] = (),
    point_weight_col: str = "WGTP",
    cost_col: str = "annual_energy_cost_adj",
    income_col: str = "income_adjusted",
) -> pd.DataFrame:
    """Gap totals per (segment, population, threshold), with replicate MOEs when available.

    Expects the frame produced by `attach_service_weights_and_eligibility_flags`
    (service weights + eligibility flags). Populations default to the fixed-charge set
    (all / <=80% AMI / <=60% SMI / <=80% AMI renters).

    Columns per row: `gap_valid_households` (positive income, non-missing cost),
    `households_in_gap` (gap > 0), `total_gap_dollars` (annual), the derived
    `mean_gap_per_household_in_gap` and `households_in_gap_rate`, and `*_moe90` for the
    two weighted estimates (NaN when replicate weights are absent).
    """
    if populations is None:
        populations = _default_populations()

    rows = []
    for threshold in thresholds:
        gap = compute_affordability_gap(
            df, threshold=threshold, cost_col=cost_col, income_col=income_col
        )
        gap_defined = gap.notna()
        gap_values = gap.fillna(0.0).to_numpy(dtype=float)
        in_gap = (gap > 0).to_numpy(dtype=bool)

        for segment, wcol in _segments_present(df).items():
            w = pd.to_numeric(df[wcol], errors="coerce").fillna(0.0)
            rep_w = _replicate_segment_weights(df, w, replicate_weight_cols, point_weight_col)
            w_arr = w.to_numpy(dtype=float)

            for pop_label, pop_fn in populations.items():
                pop = pop_fn(df).fillna(False).astype(bool).to_numpy(dtype=bool)
                valid = pop & gap_defined.to_numpy(dtype=bool)

                gap_valid_households = float(np.sum(w_arr * valid))
                households_in_gap = float(np.sum(w_arr * (valid & in_gap)))
                total_gap = float(np.sum(w_arr * gap_values * valid))

                households_moe = np.nan
                total_moe = np.nan
                if rep_w is not None:
                    factor = 4.0 / rep_w.shape[1]
                    rep_households = (valid & in_gap).astype(float) @ rep_w
                    rep_total = (gap_values * valid) @ rep_w
                    households_moe = sdr_moe_scalar(
                        households_in_gap, rep_households, factor=factor
                    )
                    total_moe = sdr_moe_scalar(total_gap, rep_total, factor=factor)

                rows.append(
                    {
                        "segment": segment,
                        "population": pop_label,
                        "threshold": float(threshold),
                        "gap_valid_households": gap_valid_households,
                        "households_in_gap": households_in_gap,
                        "households_in_gap_moe90": households_moe,
                        "households_in_gap_rate": safe_div(households_in_gap, gap_valid_households),
                        "total_gap_dollars": total_gap,
                        "total_gap_dollars_moe90": total_moe,
                        "mean_gap_per_household_in_gap": safe_div(total_gap, households_in_gap),
                    }
                )
    return pd.DataFrame(rows)


def build_affordability_gap_by_puma(
    df: pd.DataFrame,
    *,
    thresholds: Sequence[float],
    weight_col: str = "w_service",
    cost_col: str = "annual_energy_cost_adj",
    income_col: str = "income_adjusted",
) -> pd.DataFrame:
    """Service-weighted gap totals per (PUMA, threshold) for mapping.

    One row per PUMA per threshold with `gap_valid_households`, `households_in_gap`,
    `total_gap_dollars`, and `mean_gap_per_household_in_gap`.
    """
    w = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0)
    rows = []
    for threshold in thresholds:
        gap = compute_affordability_gap(
            df, threshold=threshold, cost_col=cost_col, income_col=income_col
        )
        work = pd.DataFrame(
            {
                "PUMA": df["PUMA"],
                "w_valid": w * gap.notna(),
                "w_in_gap": w * (gap > 0),
                "w_gap": w * gap.fillna(0.0),
            }
        )
        grouped = work.groupby("PUMA", as_index=False).sum()
        grouped.insert(1, "threshold", float(threshold))
        grouped = grouped.rename(
            columns={
                "w_valid": "gap_valid_households",
                "w_in_gap": "households_in_gap",
                "w_gap": "total_gap_dollars",
            }
        )
        grouped["mean_gap_per_household_in_gap"] = [
            safe_div(t, h)
            for t, h in zip(grouped["total_gap_dollars"], grouped["households_in_gap"], strict=True)
        ]
        rows.append(grouped)
    return pd.concat(rows, ignore_index=True)
