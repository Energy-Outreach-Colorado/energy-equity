"""Scenario builders for the rate-impact pipeline.

These take a household-level frame that has been augmented with:
  - `w_service`, `w_service_urban`, `w_service_rural`  (service-area weights)
  - `income_adjusted`                                  (PUMS-adjusted annual income)
  - the burden-basis cost column (`annual_energy_cost_adj`, `annual_electric_cost_adj`
    or `annual_gas_cost_adj`)
  - `energy_burden`, `burden_valid`                    (baseline burden + flag)
  - `is_low_income`, optionally `le_60_smi`            (income-qualification flags)

Two kinds of increase are supported. A `fixed` increase adds a monthly dollar amount to
each household's cost, optionally only for gas customers. A `percent` increase scales a
chosen cost column (electric, gas or total) by a fraction for households that report that
cost. Both produce newly-burdened counts at the configured thresholds, near-threshold
bands, and delta sensitivity tables. `build_scenario_sweep` re-applies the scenario for
each value in `amounts` so the user can see how impacts scale with the proposed amount.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Literal

import numpy as np
import pandas as pd

from .summaries import _bool_col, _weighted_mean, safe_div

IncreaseKind = Literal["fixed", "percent"]

INCREASE_BASE_COLUMNS = {
    "electric": "annual_electric_cost_adj",
    "gas": "annual_gas_cost_adj",
    "total": "annual_energy_cost_adj",
}


def _default_populations() -> dict[str, Callable[[pd.DataFrame], pd.Series]]:
    """Population masks used by every scenario table."""
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


def _post_burden(cost_post: np.ndarray, income: np.ndarray, nonpos_income_rule: str) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        burden_post = np.where(income > 0, cost_post / income, np.nan)
    nonpos = income <= 0
    if nonpos_income_rule == "exclude":
        burden_post = np.where(nonpos, np.nan, burden_post)
    elif nonpos_income_rule == "treat_as_high":
        burden_post = np.where(nonpos, np.inf, burden_post)
    return burden_post


def _annual_increase(
    df: pd.DataFrame,
    *,
    kind: IncreaseKind,
    amount: float,
    increase_base_col: str,
    applies: pd.Series,
) -> np.ndarray:
    if kind == "fixed":
        return 12.0 * float(amount) * applies.to_numpy(dtype=float)
    if kind == "percent":
        base = pd.to_numeric(df[increase_base_col], errors="coerce").fillna(0.0).to_numpy()
        return float(amount) * base * applies.to_numpy(dtype=float)
    raise ValueError(f"kind must be 'fixed' or 'percent', got {kind!r}")


def _applies_mask(
    df: pd.DataFrame,
    *,
    kind: IncreaseKind,
    apply_only_if_gas_positive: bool,
    monthly_gas_col: str,
    increase_base_col: str,
) -> pd.Series:
    if kind == "percent":
        return pd.to_numeric(df[increase_base_col], errors="coerce").fillna(0.0) > 0
    if apply_only_if_gas_positive:
        return pd.to_numeric(df[monthly_gas_col], errors="coerce").fillna(0.0) > 0
    return pd.Series(True, index=df.index)


def apply_rate_increase_to_microdata(
    df: pd.DataFrame,
    *,
    kind: IncreaseKind,
    amount: float,
    apply_only_if_gas_positive: bool = False,
    monthly_gas_col: str = "GASP",
    base_cost_col: str = "annual_energy_cost_adj",
    increase_base_col: str = "annual_electric_cost_adj",
    income_col: str = "income_adjusted",
    energy_burden_threshold: float = 0.06,
    high_energy_burden_threshold: float = 0.10,
    delta_thresholds_pp: Sequence[float] = (0.25, 0.50, 1.00),
    nonpos_income_rule: str = "exclude",
) -> pd.DataFrame:
    """Apply a fixed or percentage increase and attach the columns the scenario builders need.

    For `kind="fixed"`, `amount` is a monthly dollar increase applied to every household,
    or only to gas customers when `apply_only_if_gas_positive` is true. For
    `kind="percent"`, `amount` is a fraction applied to `increase_base_col` for households
    whose value in that column is positive. The increase is added to `base_cost_col`, the
    burden-basis cost, to form the post-scenario cost. Post-scenario validity requires a
    valid baseline burden so households excluded at baseline stay excluded.

    Returns the same DataFrame for chaining. Adds:
      - annual_increase, increase_applies
      - annual_energy_cost_adj_post, energy_burden_post, burden_valid_post
      - energy_burdened_post, high_energy_burdened_post
      - newly_energy_burdened, newly_high_energy_burdened
      - delta_energy_burden, delta_energy_burden_pp
      - delta_burden_ge_<pp>pp for each entry in delta_thresholds_pp
      - monthly_headroom_to_<threshold>pct
    """
    applies = _applies_mask(
        df,
        kind=kind,
        apply_only_if_gas_positive=apply_only_if_gas_positive,
        monthly_gas_col=monthly_gas_col,
        increase_base_col=increase_base_col,
    )
    df["increase_applies"] = applies
    df["annual_increase"] = _annual_increase(
        df, kind=kind, amount=amount, increase_base_col=increase_base_col, applies=applies
    )

    base = pd.to_numeric(df[base_cost_col], errors="coerce").astype(float)
    df["annual_energy_cost_adj_post"] = base + df["annual_increase"]

    income = pd.to_numeric(df[income_col], errors="coerce").astype(float).to_numpy()
    burden_post = _post_burden(
        df["annual_energy_cost_adj_post"].to_numpy(), income, nonpos_income_rule
    )

    df["energy_burden_post"] = burden_post
    df["burden_valid_post"] = ~pd.isna(burden_post) & _bool_col(df, "burden_valid")
    df["energy_burdened_post"] = (burden_post >= energy_burden_threshold) & df["burden_valid_post"]
    df["high_energy_burdened_post"] = (burden_post >= high_energy_burden_threshold) & df[
        "burden_valid_post"
    ]

    valid_both = df["burden_valid_post"]
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

    headroom_annual = energy_burden_threshold * income - base.to_numpy()
    headroom_monthly = np.where(income > 0, headroom_annual / 12.0, np.nan)
    df[f"monthly_headroom_to_{int(energy_burden_threshold * 100)}pct"] = headroom_monthly
    return df


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
    """Apply a monthly fixed-charge increase through `apply_rate_increase_to_microdata`."""
    return apply_rate_increase_to_microdata(
        df,
        kind="fixed",
        amount=monthly_increase,
        apply_only_if_gas_positive=apply_only_if_gas_positive,
        monthly_gas_col=monthly_gas_col,
        base_cost_col=base_cost_col,
        income_col=income_col,
        energy_burden_threshold=energy_burden_threshold,
        high_energy_burden_threshold=high_energy_burden_threshold,
        delta_thresholds_pp=delta_thresholds_pp,
        nonpos_income_rule=nonpos_income_rule,
    )


def build_fixed_charge_headline_summary(
    df: pd.DataFrame,
    *,
    populations: Mapping[str, Callable[[pd.DataFrame], pd.Series]] | None = None,
) -> pd.DataFrame:
    """Baseline / post / newly-burdened counts and rates per (segment, population)."""
    if populations is None:
        populations = _default_populations()
    rows = []
    applies = _bool_col(df, "increase_applies")
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
                "annual_increase_total": float(
                    (w * df["annual_increase"] * pop.astype(float)).sum()
                ),
                "avg_monthly_increase_among_applies": safe_div(
                    float((w * df["annual_increase"] * (pop & applies).astype(float)).sum()),
                    12.0 * float(w[pop & applies].sum()),
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
                ("increase_applies", "increase_applies"),
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


def build_near_threshold_summary(
    df: pd.DataFrame,
    *,
    threshold: float,
    margins_pp: Sequence[float] = (0.25, 0.50, 1.00),
    populations: Mapping[str, Callable[[pd.DataFrame], pd.Series]] | None = None,
) -> pd.DataFrame:
    """Households sitting just below the burden threshold before and after the increase.

    For each margin m (in percentage points) a household is in band when its burden lies in
    [threshold - m/100, threshold). The table reports the households in band at baseline,
    the households in band after the increase, and the households that were in band at
    baseline and crossed the threshold, per (segment, population, margin).
    """
    if populations is None:
        populations = _default_populations()
    burden = pd.to_numeric(df["energy_burden"], errors="coerce").astype(float)
    burden_post = pd.to_numeric(df["energy_burden_post"], errors="coerce").astype(float)
    newly = _bool_col(df, "newly_energy_burdened")
    rows = []
    for segment, wcol in _segments_present(df).items():
        w = pd.to_numeric(df[wcol], errors="coerce").fillna(0.0)
        for pop_label, pop_fn in populations.items():
            pop = pop_fn(df).fillna(False).astype(bool)
            valid = pop & _bool_col(df, "burden_valid") & _bool_col(df, "burden_valid_post")
            denom = float(w[valid].sum())
            for m in margins_pp:
                lower = threshold - m / 100.0
                in_band = valid & (burden >= lower) & (burden < threshold)
                in_band_post = valid & (burden_post >= lower) & (burden_post < threshold)
                crossed = in_band & newly
                rows.append(
                    {
                        "segment": segment,
                        "population": pop_label,
                        "margin_percentage_points": m,
                        "band_lower_fraction": lower,
                        "threshold_fraction": threshold,
                        "baseline_in_band_households": float(w[in_band].sum()),
                        "post_in_band_households": float(w[in_band_post].sum()),
                        "in_band_newly_burdened_households": float(w[crossed].sum()),
                        "denominator_households": denom,
                        "baseline_in_band_rate_among_burden_valid": safe_div(
                            float(w[in_band].sum()), denom
                        ),
                        "in_band_newly_burdened_rate_among_burden_valid": safe_div(
                            float(w[crossed].sum()), denom
                        ),
                    }
                )
    return pd.DataFrame(rows)


def build_scenario_sweep(
    df: pd.DataFrame,
    *,
    amounts: Sequence[float],
    kind: IncreaseKind = "fixed",
    apply_only_if_gas_positive: bool = False,
    energy_burden_threshold: float = 0.06,
    high_energy_burden_threshold: float = 0.10,
    monthly_gas_col: str = "GASP",
    base_cost_col: str = "annual_energy_cost_adj",
    increase_base_col: str = "annual_electric_cost_adj",
    income_col: str = "income_adjusted",
    populations: Mapping[str, Callable[[pd.DataFrame], pd.Series]] | None = None,
    nonpos_income_rule: str = "exclude",
) -> pd.DataFrame:
    """Sweep the scenario across alternate amounts.

    For each value in `amounts` (monthly dollars for `fixed`, fractions for `percent`),
    recompute the post-scenario burden and emit a row with counts of post-energy-burdened,
    newly-energy-burdened, etc. per (segment, population). The fixed kind also writes the
    legacy `monthly_fixed_charge_increase` column.
    """
    if populations is None:
        populations = _default_populations()

    base = pd.to_numeric(df[base_cost_col], errors="coerce").astype(float).to_numpy()
    income = pd.to_numeric(df[income_col], errors="coerce").astype(float).to_numpy()
    base_valid = _bool_col(df, "burden_valid").to_numpy()
    base_eb = _bool_col(df, "energy_burdened").to_numpy()
    base_heb = _bool_col(df, "high_energy_burdened").to_numpy()
    applies = _applies_mask(
        df,
        kind=kind,
        apply_only_if_gas_positive=apply_only_if_gas_positive,
        monthly_gas_col=monthly_gas_col,
        increase_base_col=increase_base_col,
    )

    rows = []
    for amount in amounts:
        annual = _annual_increase(
            df, kind=kind, amount=amount, increase_base_col=increase_base_col, applies=applies
        )
        burden_post = _post_burden(base + annual, income, nonpos_income_rule)
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
                row = {
                    "segment": segment,
                    "population": pop_label,
                    "increase_kind": kind,
                    "increase_amount": float(amount),
                    "annual_increase_total": float(np.sum(w * annual * pop)),
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
                if kind == "fixed":
                    row["monthly_fixed_charge_increase"] = float(amount)
                rows.append(row)
    return pd.DataFrame(rows)
