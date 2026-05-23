"""Energy and rent burden calculations.

These functions assume their input columns are already on the right scale:

- annual energy cost (sum of monthly electric × 12, monthly gas × 12, and annual other-fuel),
- annual gross rent (monthly × 12),
- adjusted annual household income (PUMS HINCP × ADJINC).

The PUMS-specific "special-code" cleaning (ACS codes 0..3 for ELEP/GASP/FULP) lives in
`energy_equity.pums.energy_cost`. Keeping the burden math separate lets callers compose
their own cost columns (e.g. a fixed-charge scenario column) without rewriting the burden
logic.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

MissingCostRule = Literal["zero", "nan"]
NonposIncomeRule = Literal["exclude", "treat_as_high"]


def add_energy_component_fields(
    df: pd.DataFrame,
    *,
    monthly_electric_col: str = "ELEP",
    monthly_gas_col: str = "GASP",
    annual_other_col: str = "FULP",
    out_annual_col: str = "energy_cost_annual",
    out_electric_col: str = "electric_cost_annual",
    out_gas_col: str = "gas_cost_annual",
    out_other_col: str = "other_cost_annual",
    missing_cost_rule: MissingCostRule = "zero",
) -> pd.DataFrame:
    """Compose annual electric, gas, other-fuel, and total energy-cost columns.

    Operates in place and returns the same DataFrame for chaining. Inputs should already
    have ACS special codes (0..3) resolved upstream; this function only handles arithmetic
    and NaN policy.
    """
    elec_monthly = df[monthly_electric_col].astype(float)
    gas_monthly = df[monthly_gas_col].astype(float)
    other_annual = df[annual_other_col].astype(float)

    if missing_cost_rule == "zero":
        elec_monthly = elec_monthly.fillna(0.0)
        gas_monthly = gas_monthly.fillna(0.0)
        other_annual = other_annual.fillna(0.0)
    elif missing_cost_rule != "nan":
        raise ValueError(f"missing_cost_rule must be 'zero' or 'nan', got {missing_cost_rule!r}")

    df[out_electric_col] = elec_monthly * 12.0
    df[out_gas_col] = gas_monthly * 12.0
    df[out_other_col] = other_annual
    df[out_annual_col] = df[out_electric_col] + df[out_gas_col] + df[out_other_col]
    return df


def compute_energy_burden(
    df: pd.DataFrame,
    *,
    cost_col: str = "energy_cost_annual",
    income_col: str = "income_adjusted",
    threshold: float = 0.06,
    high_threshold: float = 0.10,
    nonpos_income_rule: NonposIncomeRule = "exclude",
    out_burden_col: str = "energy_burden",
    out_flag_col: str = "energy_burdened",
    out_high_flag_col: str = "high_energy_burdened",
) -> pd.DataFrame:
    """Attach `energy_burden`, `energy_burdened`, `high_energy_burdened` columns.

    `nonpos_income_rule`:
      - "exclude": households with income <= 0 get NaN burden and False flags.
      - "treat_as_high": households with income <= 0 get +inf burden, both flags True.

    Operates in place and returns the same DataFrame.
    """
    if threshold <= 0 or high_threshold <= 0:
        raise ValueError("burden thresholds must be positive")
    if high_threshold <= threshold:
        raise ValueError("high_threshold must exceed threshold")

    cost = df[cost_col].astype(float).to_numpy()
    income = df[income_col].astype(float).to_numpy()
    nonpos = income <= 0

    with np.errstate(divide="ignore", invalid="ignore"):
        burden = np.where(income > 0, cost / income, np.nan)

    if nonpos_income_rule == "treat_as_high":
        burden = np.where(nonpos, np.inf, burden)
    elif nonpos_income_rule != "exclude":
        raise ValueError(
            f"nonpos_income_rule must be 'exclude' or 'treat_as_high', got {nonpos_income_rule!r}"
        )

    burdened = (burden >= threshold) & ~np.isnan(burden)
    high_burdened = (burden >= high_threshold) & ~np.isnan(burden)

    df[out_burden_col] = burden
    df[out_flag_col] = burdened
    df[out_high_flag_col] = high_burdened
    return df


def compute_rent_burden(
    df: pd.DataFrame,
    *,
    monthly_rent_col: str = "GRNTP",
    income_col: str = "income_adjusted",
    threshold: float = 0.30,
    severe_threshold: float = 0.50,
    nonpos_income_rule: NonposIncomeRule = "exclude",
    only_renters: bool = True,
    tenure_col: str | None = "TEN",
    renter_codes: tuple[int, ...] = (3, 4),
    out_rent_col: str = "rent_annual",
    out_burden_col: str = "rent_burden",
    out_flag_col: str = "rent_burdened",
    out_severe_flag_col: str = "severely_rent_burdened",
) -> pd.DataFrame:
    """Attach gross-rent burden columns. NaN burden for non-renters when `only_renters=True`.

    PUMS TEN codes: 1 = owned with mortgage, 2 = owned free and clear, 3 = rented,
    4 = occupied without payment of rent. Renter codes default to (3, 4).
    """
    if threshold <= 0 or severe_threshold <= 0:
        raise ValueError("rent burden thresholds must be positive")
    if severe_threshold <= threshold:
        raise ValueError("severe_threshold must exceed threshold")

    monthly_rent = df[monthly_rent_col].astype(float)
    annual_rent = monthly_rent * 12.0
    df[out_rent_col] = annual_rent.to_numpy()

    income = df[income_col].astype(float).to_numpy()
    nonpos = income <= 0
    with np.errstate(divide="ignore", invalid="ignore"):
        burden = np.where(income > 0, annual_rent.to_numpy() / income, np.nan)

    if nonpos_income_rule == "treat_as_high":
        burden = np.where(nonpos, np.inf, burden)
    elif nonpos_income_rule != "exclude":
        raise ValueError(
            f"nonpos_income_rule must be 'exclude' or 'treat_as_high', got {nonpos_income_rule!r}"
        )

    if only_renters:
        if tenure_col is None:
            raise ValueError("tenure_col required when only_renters=True")
        tenure = df[tenure_col].to_numpy()
        is_renter = np.isin(tenure, renter_codes)
        burden = np.where(is_renter, burden, np.nan)

    burdened = (burden >= threshold) & ~np.isnan(burden)
    severe = (burden >= severe_threshold) & ~np.isnan(burden)

    df[out_burden_col] = burden
    df[out_flag_col] = burdened
    df[out_severe_flag_col] = severe
    return df
