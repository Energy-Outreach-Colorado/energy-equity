"""Small helpers shared across reporting builders."""

from __future__ import annotations

import numpy as np
import pandas as pd


def safe_div(numerator: float, denominator: float) -> float:
    if denominator is None or denominator == 0 or pd.isna(denominator):
        return float("nan")
    return numerator / denominator


def share_below_cutoff(bin_counts: pd.Series, bin_upper_bounds: pd.Series, cutoff: float) -> float:
    """Cumulative share of households with income strictly below `cutoff`.

    `bin_counts` and `bin_upper_bounds` are aligned (the i-th count corresponds to the
    i-th upper bound). The function linearly interpolates within the bin that contains
    `cutoff`, treating bin populations as uniformly distributed in [lower, upper).
    """
    if bin_counts.empty:
        return float("nan")
    total = float(bin_counts.sum())
    if total <= 0:
        return float("nan")

    bins = pd.DataFrame(
        {"count": bin_counts.to_numpy(dtype=float), "upper": bin_upper_bounds.to_numpy(dtype=float)}
    )
    bins = bins.sort_values("upper").reset_index(drop=True)
    bins["lower"] = bins["upper"].shift(1).fillna(0.0)

    if cutoff <= 0:
        return 0.0
    cumulative = 0.0
    for _, row in bins.iterrows():
        lower, upper, count = float(row["lower"]), float(row["upper"]), float(row["count"])
        if cutoff >= upper:
            cumulative += count
            continue
        if cutoff <= lower:
            break
        # cutoff lies inside this bin -> linear interpolation.
        width = upper - lower
        if width > 0:
            cumulative += count * (cutoff - lower) / width
        break
    return cumulative / total


def bin_midpoint(
    lower: float, upper: float | None, *, fallback_for_open_top: float | None = None
) -> float:
    """Midpoint of an income bin. For the top bin (upper=None or NaN), use `fallback_for_open_top`.

    The Census B19001 top bin is `>= $200,000` with no published upper bound; for
    regressivity-curve plotting a common choice is to use ~$300,000 as a midpoint.
    """
    if upper is None or (isinstance(upper, float) and np.isnan(upper)):
        return float(fallback_for_open_top) if fallback_for_open_top is not None else float("nan")
    return (float(lower) + float(upper)) / 2.0
