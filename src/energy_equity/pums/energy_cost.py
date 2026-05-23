"""PUMS-specific income and energy-cost adjustment, plus fixed-charge scenarios.

The notebook's `compute_energy_burden` collapsed three concerns into one function:
   (a) apply ADJINC/ADJHSG inflation factors to raw PUMS dollars
   (b) sum monthly/annual cost components into a single annual energy cost
   (c) divide by income and threshold

We split (a) and (b) here as PUMS-domain helpers, then call the generic
`energy_equity.burden` module to do (c). Fixed-charge scenarios are layered on top via
`apply_fixed_charge_scenario`.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

MissingCostRule = Literal["zero", "nan"]


def apply_income_adjustment(
    df: pd.DataFrame,
    *,
    income_col: str = "HINCP",
    adjinc_col: str = "ADJINC",
    adjhsg_col: str = "ADJHSG",
    out_income_col: str = "income_adjusted",
    out_adjinc_factor: str = "adjinc_factor",
    out_adjhsg_factor: str = "adjhsg_factor",
) -> pd.DataFrame:
    """Convert PUMS-coded ADJINC/ADJHSG (integers /1_000_000) into adjustment factors.

    Adds `adjinc_factor`, `adjhsg_factor`, and the inflation-adjusted annual income
    column. Operates in place.
    """
    df[out_adjinc_factor] = df[adjinc_col].astype(float) / 1_000_000.0
    df[out_adjhsg_factor] = df[adjhsg_col].astype(float) / 1_000_000.0
    df[out_income_col] = df[income_col].astype(float) * df[out_adjinc_factor]
    return df


def apply_energy_cost_adjustment(
    df: pd.DataFrame,
    *,
    monthly_electric_col: str = "ELEP",
    monthly_gas_col: str = "GASP",
    annual_other_col: str = "FULP",
    adjhsg_factor_col: str = "adjhsg_factor",
    missing_cost_rule: MissingCostRule = "zero",
    out_electric_col: str = "annual_electric_cost_adj",
    out_gas_col: str = "annual_gas_cost_adj",
    out_other_col: str = "annual_other_fuel_cost_adj",
    out_total_col: str = "annual_energy_cost_adj",
) -> pd.DataFrame:
    """Sum inflation-adjusted electric, gas, and other-fuel costs into an annual total.

    Operates in place. Assumes `apply_income_adjustment` has already populated
    `adjhsg_factor`.
    """
    ele = df[monthly_electric_col].astype(float) * df[adjhsg_factor_col]
    gas = df[monthly_gas_col].astype(float) * df[adjhsg_factor_col]
    ful = df[annual_other_col].astype(float) * df[adjhsg_factor_col]

    if missing_cost_rule == "zero":
        ele = ele.fillna(0.0)
        gas = gas.fillna(0.0)
        ful = ful.fillna(0.0)
        df[out_electric_col] = 12.0 * ele
        df[out_gas_col] = 12.0 * gas
        df[out_other_col] = ful
        df[out_total_col] = df[out_electric_col] + df[out_gas_col] + df[out_other_col]
    elif missing_cost_rule == "nan":
        any_present = ele.notna() | gas.notna() | ful.notna()
        ele2 = ele.fillna(0.0)
        gas2 = gas.fillna(0.0)
        ful2 = ful.fillna(0.0)
        df[out_electric_col] = (12.0 * ele2).where(any_present, np.nan)
        df[out_gas_col] = (12.0 * gas2).where(any_present, np.nan)
        df[out_other_col] = ful2.where(any_present, np.nan)
        df[out_total_col] = (12.0 * (ele2 + gas2) + ful2).where(any_present, np.nan)
    else:
        raise ValueError(f"missing_cost_rule must be 'zero' or 'nan', got {missing_cost_rule!r}")
    return df


def apply_fixed_charge_scenario(
    df: pd.DataFrame,
    *,
    monthly_increase: float,
    apply_only_if_gas_positive: bool = True,
    monthly_gas_col: str = "GASP",
    base_cost_col: str = "annual_energy_cost_adj",
    out_cost_col: str = "annual_energy_cost_adj_post",
) -> pd.DataFrame:
    """Add an `*_post` annual energy cost column reflecting a monthly fixed-charge increase.

    `apply_only_if_gas_positive=True` (default) limits the increase to households that
    already report positive gas spending — matching the original notebook's behavior for a
    gas-utility fixed-charge increase. Set False to apply across all households (e.g. for
    an electric-utility scenario).

    No-op if `monthly_increase` is 0; the post column will equal the base column.
    Operates in place.
    """
    base = df[base_cost_col].astype(float)
    if monthly_increase == 0.0:
        df[out_cost_col] = base
        return df

    annual_delta = 12.0 * float(monthly_increase)
    if apply_only_if_gas_positive:
        gas_positive = df[monthly_gas_col].fillna(0.0) > 0
        df[out_cost_col] = base + annual_delta * gas_positive.astype(float)
    else:
        df[out_cost_col] = base + annual_delta
    return df


def compute_energy_burden_pums(
    df: pd.DataFrame,
    *,
    threshold: float = 0.06,
    high_threshold: float = 0.10,
    nonpos_income_rule: Literal["exclude", "treat_as_high"] = "exclude",
    cost_col: str = "annual_energy_cost_adj",
    income_col: str = "income_adjusted",
    out_burden_col: str = "energy_burden",
    out_valid_col: str = "burden_valid",
    out_flag_col: str = "energy_burdened",
    out_high_flag_col: str = "high_energy_burdened",
) -> pd.DataFrame:
    """PUMS-flavored energy burden: divides adjusted cost by adjusted income and flags."""
    income = df[income_col].astype(float).to_numpy()
    cost = df[cost_col].astype(float).to_numpy()

    with np.errstate(divide="ignore", invalid="ignore"):
        burden = np.where(income > 0, cost / income, np.nan)

    nonpos = income <= 0
    if nonpos_income_rule == "exclude":
        burden = np.where(nonpos, np.nan, burden)
    elif nonpos_income_rule == "treat_as_high":
        burden = np.where(nonpos, np.inf, burden)
    else:
        raise ValueError(
            f"nonpos_income_rule must be 'exclude' or 'treat_as_high', got {nonpos_income_rule!r}"
        )

    df[out_burden_col] = burden
    df[out_valid_col] = ~pd.isna(burden)
    df[out_flag_col] = (burden >= threshold) & ~pd.isna(burden)
    df[out_high_flag_col] = (burden >= high_threshold) & ~pd.isna(burden)
    return df


def compute_post_scenario_burden_and_deltas(
    df: pd.DataFrame,
    *,
    threshold: float = 0.06,
    high_threshold: float = 0.10,
    nonpos_income_rule: Literal["exclude", "treat_as_high"] = "exclude",
    delta_thresholds_pp: tuple[float, ...] = (0.25, 0.50),
    post_cost_col: str = "annual_energy_cost_adj_post",
    income_col: str = "income_adjusted",
    base_burden_col: str = "energy_burden",
    base_flag_col: str = "energy_burdened",
    base_high_flag_col: str = "high_energy_burdened",
    base_valid_col: str = "burden_valid",
) -> pd.DataFrame:
    """Compute the *_post burden + flags, plus "newly burdened" and delta-pp sensitivity flags.

    Writes columns:
      - energy_burden_post, burden_valid_post, energy_burdened_post, high_energy_burdened_post
      - newly_energy_burdened, newly_high_energy_burdened
      - delta_energy_burden
      - delta_burden_ge_<pp>pp for each entry in `delta_thresholds_pp`
    """
    income = df[income_col].astype(float).to_numpy()
    cost_post = df[post_cost_col].astype(float).to_numpy()

    with np.errstate(divide="ignore", invalid="ignore"):
        burden_post = np.where(income > 0, cost_post / income, np.nan)
    nonpos = income <= 0
    if nonpos_income_rule == "exclude":
        burden_post = np.where(nonpos, np.nan, burden_post)
    elif nonpos_income_rule == "treat_as_high":
        burden_post = np.where(nonpos, np.inf, burden_post)
    else:
        raise ValueError(
            f"nonpos_income_rule must be 'exclude' or 'treat_as_high', got {nonpos_income_rule!r}"
        )

    df["energy_burden_post"] = burden_post
    df["burden_valid_post"] = ~pd.isna(burden_post)
    df["energy_burdened_post"] = (burden_post >= threshold) & ~pd.isna(burden_post)
    df["high_energy_burdened_post"] = (burden_post >= high_threshold) & ~pd.isna(burden_post)

    valid_both = df[base_valid_col].to_numpy(dtype=bool) & df["burden_valid_post"].to_numpy(
        dtype=bool
    )
    df["newly_energy_burdened"] = (
        valid_both
        & df["energy_burdened_post"].to_numpy(dtype=bool)
        & ~df[base_flag_col].to_numpy(dtype=bool)
    )
    df["newly_high_energy_burdened"] = (
        valid_both
        & df["high_energy_burdened_post"].to_numpy(dtype=bool)
        & ~df[base_high_flag_col].to_numpy(dtype=bool)
    )

    base_burden = df[base_burden_col].astype(float).to_numpy()
    delta = burden_post - base_burden
    df["delta_energy_burden"] = np.where(valid_both, delta, np.nan)

    for pp in delta_thresholds_pp:
        col = f"delta_burden_ge_{pp:.2f}".replace(".", "_") + "pp"
        df[col] = df["delta_energy_burden"] >= (pp / 100.0)
    return df
