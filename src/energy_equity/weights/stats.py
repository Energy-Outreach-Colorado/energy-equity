"""Weighted descriptive statistics: totals, means, quantiles, and count/rate estimates."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from .sdr import PUMS_REPLICATE_FACTOR, Z_90, sdr_moe


def _coerce(values: Sequence[float] | np.ndarray | pd.Series) -> np.ndarray:
    return np.asarray(values, dtype=float)


def weighted_total(
    values: Sequence[float] | np.ndarray | pd.Series,
    weights: Sequence[float] | np.ndarray | pd.Series,
    *,
    ignore_nan: bool = True,
) -> float:
    """Weighted sum: sum(values * weights), optionally masking NaNs in either array."""
    v = _coerce(values)
    w = _coerce(weights)
    if v.shape != w.shape:
        raise ValueError(f"values and weights must have the same shape; got {v.shape} vs {w.shape}")
    if ignore_nan:
        mask = ~(np.isnan(v) | np.isnan(w))
        return float((v[mask] * w[mask]).sum())
    return float((v * w).sum())


def weighted_mean(
    values: Sequence[float] | np.ndarray | pd.Series,
    weights: Sequence[float] | np.ndarray | pd.Series,
    *,
    ignore_nan: bool = True,
) -> float:
    """Weighted mean: weighted_total / sum(weights). NaN if total weight is zero."""
    v = _coerce(values)
    w = _coerce(weights)
    if ignore_nan:
        mask = ~(np.isnan(v) | np.isnan(w))
        v, w = v[mask], w[mask]
    total_w = float(w.sum())
    if total_w == 0.0:
        return float("nan")
    return float((v * w).sum() / total_w)


def weighted_quantile(
    values: Sequence[float] | np.ndarray | pd.Series,
    weights: Sequence[float] | np.ndarray | pd.Series,
    q: float,
    *,
    ignore_nan: bool = True,
) -> float:
    """Weighted quantile via cumulative-weight linear interpolation.

    Follows the "Type 7" definition (the default in R and numpy's `quantile`), generalized
    to weighted samples: positions are placed at cumulative weight minus half the row's
    weight, then linearly interpolated.
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")
    v = _coerce(values)
    w = _coerce(weights)
    if v.shape != w.shape:
        raise ValueError(f"values and weights must have the same shape; got {v.shape} vs {w.shape}")
    if ignore_nan:
        mask = ~(np.isnan(v) | np.isnan(w))
        v, w = v[mask], w[mask]
    if v.size == 0:
        return float("nan")
    order = np.argsort(v)
    v_sorted = v[order]
    w_sorted = w[order]
    total = w_sorted.sum()
    if total <= 0:
        return float("nan")
    cum = np.cumsum(w_sorted)
    # Mid-rank position for each value, then normalize to [0, 1].
    pos = (cum - 0.5 * w_sorted) / total
    return float(np.interp(q, pos, v_sorted))


def weighted_median(
    values: Sequence[float] | np.ndarray | pd.Series,
    weights: Sequence[float] | np.ndarray | pd.Series,
    *,
    ignore_nan: bool = True,
) -> float:
    """Convenience wrapper for the 0.5 quantile."""
    return weighted_quantile(values, weights, 0.5, ignore_nan=ignore_nan)


def estimate_count_and_rate(
    df: pd.DataFrame,
    mask: pd.Series | np.ndarray,
    *,
    weight_col: str,
    replicate_weight_cols: Sequence[str] | None = None,
    universe_mask: pd.Series | np.ndarray | None = None,
    factor: float = PUMS_REPLICATE_FACTOR,
    z: float = Z_90,
) -> dict[str, float]:
    """Return a weighted count, rate, and 90% MOEs for the rows matching `mask`.

    Args:
        df: Source frame.
        mask: Boolean selector for the numerator (e.g. energy-burdened households).
        weight_col: Name of the point-estimate weight column (e.g. "WGTP").
        replicate_weight_cols: Optional sequence of 80 replicate weight columns. When
            provided, MOEs are computed via the SDR formula; otherwise MOEs are NaN.
        universe_mask: Optional denominator mask. Defaults to all rows.
        factor: SDR variance factor (4/R).
        z: z-multiplier for the MOE (default 1.645 = 90%).

    Returns:
        Dict with keys: `count`, `count_moe`, `universe`, `universe_moe`, `rate`,
        `rate_moe`, `unweighted_n`.
    """
    mask = np.asarray(mask, dtype=bool)
    universe = (
        np.asarray(universe_mask, dtype=bool)
        if universe_mask is not None
        else np.ones(len(df), dtype=bool)
    )
    if len(mask) != len(df) or len(universe) != len(df):
        raise ValueError("mask and universe_mask must have the same length as df")

    weights = df[weight_col].to_numpy(dtype=float)
    count = float(weights[mask].sum())
    uni = float(weights[universe].sum())
    rate = count / uni if uni > 0 else float("nan")

    out: dict[str, float] = {
        "count": count,
        "universe": uni,
        "rate": rate,
        "unweighted_n": float(int(mask.sum())),
        "count_moe": float("nan"),
        "universe_moe": float("nan"),
        "rate_moe": float("nan"),
    }

    if replicate_weight_cols:
        reps = df[list(replicate_weight_cols)].to_numpy(dtype=float)  # (N, R)
        count_reps = reps[mask].sum(axis=0)  # (R,)
        uni_reps = reps[universe].sum(axis=0)  # (R,)
        rate_reps = np.where(uni_reps > 0, count_reps / uni_reps, np.nan)

        out["count_moe"] = float(
            sdr_moe(np.array([count]), count_reps[np.newaxis, :], factor=factor, z=z)[0]
        )
        out["universe_moe"] = float(
            sdr_moe(np.array([uni]), uni_reps[np.newaxis, :], factor=factor, z=z)[0]
        )
        if not np.isnan(rate):
            out["rate_moe"] = float(
                sdr_moe(np.array([rate]), rate_reps[np.newaxis, :], factor=factor, z=z)[0]
            )
    return out
