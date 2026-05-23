"""Generic groupby + SDR-MOE table builder for household-level PUMS microdata.

The builder takes prepared household microdata (a `HouseholdMicrodata`), a list of group
columns (e.g. `["PUMA"]` for the PUMA-level table or `["PUMA", "head_race"]` for a
demographic cut), and a metric specification. It returns a DataFrame with one row per
group containing weighted counts, rates, and replicate-based 90% margins of error.

The metric specification lives in `tables.metrics` so it can be shared between this base
table builder and the demographic / scenario builders.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..pums.prepare import HouseholdMicrodata
from ..weights.sdr import PUMS_REPLICATE_FACTOR, Z_90, sdr_moe


@dataclass(frozen=True)
class CountMetric:
    """A weighted count metric defined by a `(rows) -> mask` function.

    The mask should be a 0/1 (or fractional, for AMI expected-probability) array of length
    `len(df)`. The builder multiplies the mask by the point-estimate weight per row, sums
    by group, then repeats for each replicate weight to produce MOEs.
    """

    name: str
    mask_fn: Callable[[pd.DataFrame], np.ndarray]


@dataclass(frozen=True)
class RatioMetric:
    """A rate computed as `numerator_metric / denominator_metric`.

    Rates carry MOEs computed by recomputing the ratio under each replicate weight (not by
    propagating numerator and denominator MOEs in isolation).
    """

    name: str
    numerator: str
    denominator: str


def _ensure_columns(df: pd.DataFrame, names: Sequence[str], context: str) -> None:
    missing = [n for n in names if n not in df.columns]
    if missing:
        raise KeyError(f"build_table: {context} missing required columns: {missing}")


def build_table(
    microdata: HouseholdMicrodata,
    group_cols: Sequence[str],
    *,
    count_metrics: Sequence[CountMetric],
    ratio_metrics: Sequence[RatioMetric] = (),
    compute_moe: bool = True,
    suppress_small_n: bool = True,
    min_unweighted_n: int = 30,
    suppress_rates_only: bool = True,
    return_replicates: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Group `microdata.df` by `group_cols` and aggregate the given metrics.

    Returns a DataFrame with one row per group containing:
      - `n_unweighted`: count of source rows in the group
      - one `<metric>_w` column per `CountMetric`
      - one `<ratio>` column per `RatioMetric`
      - when `compute_moe=True` and `microdata.replicate_weight_cols` is populated,
        a `<metric>_w_moe90` column per metric and a `<ratio>_moe90` column per ratio
      - when `suppress_small_n=True`, a `small_n_flag` column; rate columns are set NaN
        in flagged rows when `suppress_rates_only=True`.
    """
    df = microdata.df
    group_cols = list(group_cols)
    _ensure_columns(df, group_cols, context="group_cols")

    weight_col = microdata.point_weight_col
    rep_cols = list(microdata.replicate_weight_cols) if compute_moe else []

    # Precompute mask arrays once per metric (no copies of the underlying frame).
    masks: dict[str, np.ndarray] = {}
    for cm in count_metrics:
        arr = np.asarray(cm.mask_fn(df), dtype=float)
        if arr.shape != (len(df),):
            raise ValueError(
                f"Metric {cm.name!r} mask returned shape {arr.shape}, expected ({len(df)},)"
            )
        masks[cm.name] = arr

    # Point estimate: build a tmp frame with one weighted-mask column per metric, then sum.
    point_frame = df[group_cols].copy()
    point_frame["__n_unweighted__"] = 1
    w = df[weight_col].to_numpy(dtype=float)
    for cm in count_metrics:
        point_frame[f"{cm.name}_w"] = w * masks[cm.name]

    base = point_frame.groupby(group_cols, dropna=False).sum(numeric_only=True).reset_index()
    base = base.rename(columns={"__n_unweighted__": "n_unweighted"})
    base["n_unweighted"] = base["n_unweighted"].astype(int)

    # Ratios at point estimate.
    for rm in ratio_metrics:
        num = base[f"{rm.numerator}_w"].to_numpy(dtype=float)
        den = base[f"{rm.denominator}_w"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            base[rm.name] = np.where(den > 0, num / den, np.nan)

    # MOEs via replicate weights. `rep_estimates` is populated only when MOEs are
    # requested; it is also the in-memory hand-off into the service-allocation pipeline.
    rep_estimates: dict[str, np.ndarray] = {}
    if compute_moe and rep_cols:
        rep_estimates = {
            cm.name: np.zeros((len(base), len(rep_cols)), dtype=float) for cm in count_metrics
        }
        rep_ratios: dict[str, np.ndarray] = {
            rm.name: np.zeros((len(base), len(rep_cols)), dtype=float) for rm in ratio_metrics
        }
        base_index = base.set_index(group_cols).index

        rep_weights = df[rep_cols].to_numpy(dtype=float)  # (N, R)
        for r_idx in range(len(rep_cols)):
            wr = rep_weights[:, r_idx]
            rep_frame = df[group_cols].copy()
            for cm in count_metrics:
                rep_frame[f"{cm.name}_w"] = wr * masks[cm.name]
            rep_agg = rep_frame.groupby(group_cols, dropna=False).sum(numeric_only=True)
            rep_agg = rep_agg.reindex(base_index, fill_value=0.0)
            for cm in count_metrics:
                rep_estimates[cm.name][:, r_idx] = rep_agg[f"{cm.name}_w"].to_numpy()

            for rm in ratio_metrics:
                num = rep_agg[f"{rm.numerator}_w"].to_numpy(dtype=float)
                den = rep_agg[f"{rm.denominator}_w"].to_numpy(dtype=float)
                with np.errstate(divide="ignore", invalid="ignore"):
                    rep_ratios[rm.name][:, r_idx] = np.where(den > 0, num / den, np.nan)

        for cm in count_metrics:
            point = base[f"{cm.name}_w"].to_numpy(dtype=float)
            base[f"{cm.name}_w_moe90"] = sdr_moe(
                point, rep_estimates[cm.name], factor=PUMS_REPLICATE_FACTOR, z=Z_90
            )

        for rm in ratio_metrics:
            point = base[rm.name].to_numpy(dtype=float)
            reps = rep_ratios[rm.name]
            finite_count = np.sum(np.isfinite(reps), axis=1)
            ok = np.isfinite(point) & (finite_count >= max(1, len(rep_cols) // 2))
            moe = np.full(len(base), np.nan, dtype=float)
            if ok.any():
                moe[ok] = sdr_moe(point[ok], reps[ok, :], factor=PUMS_REPLICATE_FACTOR, z=Z_90)
            base[f"{rm.name}_moe90"] = moe

    if suppress_small_n:
        base["small_n_flag"] = base["n_unweighted"] < min_unweighted_n
        if suppress_rates_only:
            rate_cols = [c for c in base.columns if c.startswith("pct_")]
            moe_rate_cols = [
                c for c in base.columns if c.startswith("pct_") and c.endswith("_moe90")
            ]
            suppress_targets = list({*rate_cols, *moe_rate_cols})
            base.loc[base["small_n_flag"], suppress_targets] = np.nan

    # Sort: by all group cols ascending, then largest groups first by total. The
    # replicate matrices are reordered alongside so row i of `rep_estimates[name]`
    # corresponds to row i of the returned DataFrame.
    if "hh_total_w" in base.columns:
        sort_cols = group_cols + ["hh_total_w"]
        ascending = [True] * len(group_cols) + [False]
        sort_index = base.sort_values(sort_cols, ascending=ascending).index.to_numpy()
        base = base.iloc[sort_index].reset_index(drop=True)
        for name in list(rep_estimates.keys()):
            rep_estimates[name] = rep_estimates[name][sort_index, :]

    if return_replicates:
        return base, rep_estimates
    return base
