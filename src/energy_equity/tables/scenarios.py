"""Scenario builders for the fixed-charge rate-impact pipeline.

These take a household-level frame that has been augmented with:
  - `w_service`, `w_service_urban`, `w_service_rural`  (service-area weights)
  - `income_adjusted`                                  (PUMS-adjusted annual income)
  - `annual_energy_cost_adj`                           (cleaned annual energy cost)
  - `energy_burden`, `burden_valid`                    (baseline burden + flag)
  - `is_low_income`, optionally `le_60_smi`            (income-qualification flags)

The scenario applies a configurable monthly fixed-charge delta and produces
newly-burdened counts at the 6% and 10% thresholds. `build_scenario_sweep` re-applies
the scenario for each value in `monthly_increases` so the user can see how impacts
scale with the rate-case proposed amount.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from .summaries import _bool_col, _weighted_mean, safe_div


# Population definitions used by the fixed-charge pipeline. Mirrors the notebook's
# POPULATION_DEFINITIONS but kept here as a separate module-level constant so callers
# can pick a subset for their analysis.
def _default_populations() -> dict[str, Callable[[pd.DataFrame], pd.Series]]:
    return {
        "All households": lambda d: pd.Series(True, index=d.index),
        "<=80% AMI": lambda d: _bool_col(d, "is_low_income"),
        "<=60% SMI": lambda d: _bool_col(d, "le_60_smi"),
        "<=80% AMI renters": lambda d: _bool_col(d, "is_low_income") & (d.get("TEN", 0) == 3),
    }


def _segments_present(df: pd.DataFrame) -> dict[str, str]:
    out = {"all": "w_service"}
    for seg in ("urban", "rural"):
        col = f"w_service_{seg}"
        if col in df.columns and float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum()) > 0:
            out[seg] = col
    return out


def apply_fixed_charge_to_microdata(
    df: pd.DataFrame,
    *,
    monthly_increase: float,
    apply_only_if_gas_positive: bool = True,
    monthly_gas_col: str = "GASP",
    base_cost_col: str = "annual_energy_cost_adj",
    income_col: str = "income_adjusted",
    energy_burden_threshold: float = 0.06,
    high_energy_burden_threshold: float = 0.10,
    delta_thresholds_pp: Sequence[float] = (0.25, 0.50, 1.00),
    nonpos_income_rule: str = "exclude",
) -> pd.DataFrame:
    """Apply a fixed-charge scenario and attach all the columns the scenario builders need.

    Returns the same DataFrame for chaining. Adds:
      - annual_fixed_charge_increase, fixed_charge_applies
      - annual_energy_cost_adj_post, energy_burden_post, burden_valid_post
      - energy_burdened_post, high_energy_burdened_post
      - newly_energy_burdened, newly_high_energy_burdened
      - delta_energy_burden, delta_energy_burden_pp
      - delta_burden_ge_<pp>pp for each entry in delta_thresholds_pp
      - monthly_headroom_to_<threshold>pct  (for headroom analysis)
    """
    if apply_only_if_gas_positive:
        applies = pd.to_numeric(df[monthly_gas_col], errors="coerce").fillna(0.0) > 0
    else:
        applies = pd.Series(True, index=df.index)
    df["fixed_charge_applies"] = applies
    df["annual_fixed_charge_increase"] = 12.0 * float(monthly_increase) * applies.astype(float)

    base = pd.to_numeric(df[base_cost_col], errors="coerce").astype(float)
    df["annual_energy_cost_adj_post"] = base + df["annual_fixed_charge_increase"]

    income = pd.to_numeric(df[income_col], errors="coerce").astype(float).to_numpy()
    cost_post = df["annual_energy_cost_adj_post"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        burden_post = np.where(income > 0, cost_post / income, np.nan)
    nonpos = income <= 0
    if nonpos_income_rule == "exclude":
        burden_post = np.where(nonpos, np.nan, burden_post)
    elif nonpos_income_rule == "treat_as_high":
        burden_post = np.where(nonpos, np.inf, burden_post)

    df["energy_burden_post"] = burden_post
    df["burden_valid_post"] = ~pd.isna(burden_post)
    df["energy_burdened_post"] = (burden_post >= energy_burden_threshold) & df["burden_valid_post"]
    df["high_energy_burdened_post"] = (burden_post >= high_energy_burden_threshold) & df[
        "burden_valid_post"
    ]

    base_valid = _bool_col(df, "burden_valid")
    valid_both = base_valid & df["burden_valid_post"]
    df["newly_energy_burdened"] = (
        valid_both & df["energy_burdened_post"] & (~_bool_col(df, "energy_burdened"))
    )
    df["newly_high_energy_burdened"] = (
        valid_both & df["high_energy_burdened_post"] & (~_bool_col(df, "high_energy_burdened"))
    )

    delta = (
        burden_post - pd.to_numeric(df["energy_burden"], errors="coerce").astype(float).to_numpy()
    )
    df["delta_energy_burden"] = np.where(valid_both, delta, np.nan)
    df["delta_energy_burden_pp"] = df["delta_energy_burden"] * 100.0

    for pp in delta_thresholds_pp:
        col = f"delta_burden_ge_{pp:.2f}".replace(".", "_") + "pp"
        df[col] = df["delta_energy_burden"] >= (pp / 100.0)

    # Monthly headroom to the 6% threshold for households still below it.
    monthly_income = income / 12.0
    headroom_annual = energy_burden_threshold * income - base
    headroom_monthly = np.where(income > 0, headroom_annual / 12.0, np.nan)
    df[f"monthly_headroom_to_{int(energy_burden_threshold * 100)}pct"] = headroom_monthly
    _ = monthly_income  # kept for potential future per-month analysis
    return df


def build_fixed_charge_headline_summary(
    df: pd.DataFrame,
    *,
    populations: Mapping[str, Callable[[pd.DataFrame], pd.Series]] | None = None,
) -> pd.DataFrame:
    """Baseline / post / newly-burdened counts and rates per (segment, population)."""
    if populations is None:
        populations = _default_populations()
    rows = []
    for segment, wcol in _segments_present(df).items():
        w = pd.to_numeric(df[wcol], errors="coerce").fillna(0.0)
        for pop_label, pop_fn in populations.items():
            pop = pop_fn(df).fillna(False).astype(bool)
            valid = pop & _bool_col(df, "burden_valid") & _bool_col(df, "burden_valid_post")

            burden_valid_hh = float(w[valid].sum())
            row = {
                "segment": segment,
                "population": pop_label,
                "population_households": float(w[pop].sum()),
                "burden_valid_households": burden_valid_hh,
                "annual_fixed_charge_increase_total": float(
                    (w * df["annual_fixed_charge_increase"] * pop.astype(float)).sum()
                ),
                "avg_baseline_energy_burden": _weighted_mean(
                    df.loc[valid, "energy_burden"], w[valid]
                ),
                "avg_post_energy_burden": _weighted_mean(
                    df.loc[valid, "energy_burden_post"], w[valid]
                ),
                "avg_delta_energy_burden_pp": _weighted_mean(
                    df.loc[valid, "delta_energy_burden_pp"], w[valid]
                ),
            }
            for metric_name, col in [
                ("baseline_energy_burdened", "energy_burdened"),
                ("post_energy_burdened", "energy_burdened_post"),
                ("newly_energy_burdened", "newly_energy_burdened"),
                ("baseline_highly_energy_burdened", "high_energy_burdened"),
                ("post_highly_energy_burdened", "high_energy_burdened_post"),
                ("newly_highly_energy_burdened", "newly_high_energy_burdened"),
                ("fixed_charge_applies", "fixed_charge_applies"),
            ]:
                households = float(w[valid & _bool_col(df, col)].sum())
                row[f"{metric_name}_households"] = households
                row[f"{metric_name}_rate_among_burden_valid"] = safe_div(
                    households, burden_valid_hh
                )
            rows.append(row)
    return pd.DataFrame(rows)


def build_delta_sensitivity_summary(
    df: pd.DataFrame,
    *,
    delta_thresholds_pp: Sequence[float] = (0.25, 0.50, 1.00),
    populations: Mapping[str, Callable[[pd.DataFrame], pd.Series]] | None = None,
) -> pd.DataFrame:
    """Households with burden increase >= each threshold (in percentage points)."""
    if populations is None:
        populations = _default_populations()
    rows = []
    for segment, wcol in _segments_present(df).items():
        w = pd.to_numeric(df[wcol], errors="coerce").fillna(0.0)
        for pop_label, pop_fn in populations.items():
            pop = pop_fn(df).fillna(False).astype(bool)
            valid = pop & _bool_col(df, "burden_valid") & _bool_col(df, "burden_valid_post")
            denom = float(w[valid].sum())
            for pp in delta_thresholds_pp:
                col = f"delta_burden_ge_{pp:.2f}".replace(".", "_") + "pp"
                hh = float(w[valid & _bool_col(df, col)].sum())
                rows.append(
                    {
                        "segment": segment,
                        "population": pop_label,
                        "delta_threshold_fraction": pp / 100.0,
                        "delta_threshold_percentage_points": pp,
                        "households": hh,
                        "denominator_households": denom,
                        "rate_among_burden_valid": safe_div(hh, denom),
                    }
                )
    return pd.DataFrame(rows)


def build_scenario_sweep(
    df: pd.DataFrame,
    *,
    monthly_increases: Sequence[float],
    apply_only_if_gas_positive: bool,
    energy_burden_threshold: float,
    high_energy_burden_threshold: float,
    monthly_gas_col: str = "GASP",
    base_cost_col: str = "annual_energy_cost_adj",
    income_col: str = "income_adjusted",
    populations: Mapping[str, Callable[[pd.DataFrame], pd.Series]] | None = None,
    nonpos_income_rule: str = "exclude",
) -> pd.DataFrame:
    """Sweep the fixed-charge scenario across alternate monthly amounts.

    For each value in `monthly_increases`, recompute `*_post` columns and emit a row with
    counts of post-energy-burdened, newly-energy-burdened, etc. per (segment, population).
    Useful for plotting impact-vs-increase curves in rate-case filings.
    """
    if populations is None:
        populations = _default_populations()

    # Compute the base inputs once.
    base = pd.to_numeric(df[base_cost_col], errors="coerce").astype(float).to_numpy()
    income = pd.to_numeric(df[income_col], errors="coerce").astype(float).to_numpy()
    base_valid = _bool_col(df, "burden_valid").to_numpy()
    base_eb = _bool_col(df, "energy_burdened").to_numpy()
    base_heb = _bool_col(df, "high_energy_burdened").to_numpy()

    if apply_only_if_gas_positive:
        applies_mask = (
            pd.to_numeric(df[monthly_gas_col], errors="coerce").fillna(0.0) > 0
        ).to_numpy()
    else:
        applies_mask = np.ones(len(df), dtype=bool)

    rows = []
    for inc in monthly_increases:
        annual_delta = 12.0 * float(inc) * applies_mask
        cost_post = base + annual_delta
        with np.errstate(divide="ignore", invalid="ignore"):
            burden_post = np.where(income > 0, cost_post / income, np.nan)
        nonpos = income <= 0
        if nonpos_income_rule == "exclude":
            burden_post = np.where(nonpos, np.nan, burden_post)
        elif nonpos_income_rule == "treat_as_high":
            burden_post = np.where(nonpos, np.inf, burden_post)

        valid_post = ~np.isnan(burden_post)
        eb_post = (burden_post >= energy_burden_threshold) & valid_post
        heb_post = (burden_post >= high_energy_burden_threshold) & valid_post
        valid_both = base_valid & valid_post
        new_eb = valid_both & eb_post & ~base_eb
        new_heb = valid_both & heb_post & ~base_heb

        for segment, wcol in _segments_present(df).items():
            w = pd.to_numeric(df[wcol], errors="coerce").fillna(0.0).to_numpy()
            for pop_label, pop_fn in populations.items():
                pop = pop_fn(df).fillna(False).astype(bool).to_numpy()
                m = pop & valid_both
                rows.append(
                    {
                        "segment": segment,
                        "population": pop_label,
                        "monthly_fixed_charge_increase": float(inc),
                        "post_energy_burdened_households": float(np.sum(w * (eb_post & m))),
                        "newly_energy_burdened_households": float(np.sum(w * (new_eb & m))),
                        "post_highly_energy_burdened_households": float(np.sum(w * (heb_post & m))),
                        "newly_highly_energy_burdened_households": float(np.sum(w * (new_heb & m))),
                        "burden_valid_households": float(np.sum(w * m)),
                        "newly_energy_burdened_rate": safe_div(
                            float(np.sum(w * (new_eb & m))), float(np.sum(w * m))
                        ),
                        "newly_highly_energy_burdened_rate": safe_div(
                            float(np.sum(w * (new_heb & m))), float(np.sum(w * m))
                        ),
                    }
                )
    return pd.DataFrame(rows)
