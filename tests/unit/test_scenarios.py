"""Tests for the rate-increase scenario builders."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_equity.tables.scenarios import (
    apply_fixed_charge_to_microdata,
    apply_rate_increase_to_microdata,
    build_fixed_charge_headline_summary,
    build_near_threshold_summary,
    build_scenario_sweep,
)


@pytest.fixture
def households() -> pd.DataFrame:
    """Five households with a 3 percent electric burden threshold in mind.

    Burdens at baseline are 2.9, 2.8, 2.0, 5.0 and undefined (electricity in rent).
    """
    df = pd.DataFrame(
        {
            "income_adjusted": [100_000.0, 50_000.0, 60_000.0, 20_000.0, 40_000.0],
            "annual_electric_cost_adj": [2_900.0, 1_400.0, 1_200.0, 1_000.0, 0.0],
            "annual_gas_cost_adj": [600.0, 0.0, 300.0, 200.0, 0.0],
            "annual_energy_cost_adj": [3_500.0, 1_400.0, 1_500.0, 1_200.0, 0.0],
            "ELEP": [241.7, 116.7, 100.0, 83.3, np.nan],
            "GASP": [50.0, 0.0, 25.0, 16.7, np.nan],
            "w_service": [10.0, 10.0, 10.0, 10.0, 10.0],
            "is_low_income": [False, True, True, True, True],
            "le_60_smi": [False, True, False, True, True],
            "TEN": [1, 3, 1, 3, 3],
        }
    )
    burden = df["annual_electric_cost_adj"] / df["income_adjusted"]
    burden[df["ELEP"].isna()] = np.nan
    df["energy_burden"] = burden
    df["burden_valid"] = burden.notna()
    df["energy_burdened"] = (burden >= 0.03).fillna(False)
    df["high_energy_burdened"] = (burden >= 0.06).fillna(False)
    return df


def test_percent_increase_scales_electric_cost_for_payers(households: pd.DataFrame) -> None:
    apply_rate_increase_to_microdata(
        households,
        kind="percent",
        amount=0.10,
        base_cost_col="annual_electric_cost_adj",
        increase_base_col="annual_electric_cost_adj",
        energy_burden_threshold=0.03,
        high_energy_burden_threshold=0.06,
    )
    assert households["annual_increase"].tolist() == pytest.approx(
        [290.0, 140.0, 120.0, 100.0, 0.0]
    )
    assert households["increase_applies"].tolist() == [True, True, True, True, False]
    assert households["energy_burden_post"].iloc[0] == pytest.approx(0.0319)
    assert bool(households["newly_energy_burdened"].iloc[0])
    assert bool(households["newly_energy_burdened"].iloc[1])
    assert not bool(households["newly_energy_burdened"].iloc[2])
    assert not bool(households["newly_energy_burdened"].iloc[3])
    assert not bool(households["burden_valid_post"].iloc[4])


def test_fixed_wrapper_matches_generic_fixed_kind(households: pd.DataFrame) -> None:
    a = households.copy()
    b = households.copy()
    apply_fixed_charge_to_microdata(
        a, monthly_increase=5.0, apply_only_if_gas_positive=True, energy_burden_threshold=0.03
    )
    apply_rate_increase_to_microdata(
        b, kind="fixed", amount=5.0, apply_only_if_gas_positive=True, energy_burden_threshold=0.03
    )
    pd.testing.assert_series_equal(a["annual_increase"], b["annual_increase"])
    assert a["annual_increase"].tolist() == pytest.approx([60.0, 0.0, 60.0, 60.0, 0.0])


def test_headline_reports_average_monthly_increase(households: pd.DataFrame) -> None:
    apply_rate_increase_to_microdata(
        households,
        kind="percent",
        amount=0.10,
        base_cost_col="annual_electric_cost_adj",
        energy_burden_threshold=0.03,
        high_energy_burden_threshold=0.06,
    )
    headline = build_fixed_charge_headline_summary(households)
    row = headline[(headline["segment"] == "all") & (headline["population"] == "All households")]
    assert row["annual_increase_total"].iloc[0] == pytest.approx(10 * 650.0)
    assert row["avg_monthly_increase_among_applies"].iloc[0] == pytest.approx(650.0 / 4 / 12)
    assert row["newly_energy_burdened_households"].iloc[0] == pytest.approx(20.0)
    assert row["burden_valid_households"].iloc[0] == pytest.approx(40.0)


def test_near_threshold_bands(households: pd.DataFrame) -> None:
    apply_rate_increase_to_microdata(
        households,
        kind="percent",
        amount=0.10,
        base_cost_col="annual_electric_cost_adj",
        energy_burden_threshold=0.03,
        high_energy_burden_threshold=0.06,
    )
    near = build_near_threshold_summary(households, threshold=0.03, margins_pp=(0.25, 1.0))
    all_rows = near[(near["segment"] == "all") & (near["population"] == "All households")]
    quarter = all_rows[all_rows["margin_percentage_points"] == 0.25].iloc[0]
    one = all_rows[all_rows["margin_percentage_points"] == 1.0].iloc[0]
    assert quarter["baseline_in_band_households"] == pytest.approx(20.0)
    assert quarter["in_band_newly_burdened_households"] == pytest.approx(20.0)
    assert one["baseline_in_band_households"] == pytest.approx(30.0)
    assert one["post_in_band_households"] == pytest.approx(10.0)


def test_sweep_percent_rows_and_monotonicity(households: pd.DataFrame) -> None:
    sweep = build_scenario_sweep(
        households,
        amounts=(0.0, 0.05, 0.10, 0.20),
        kind="percent",
        base_cost_col="annual_electric_cost_adj",
        energy_burden_threshold=0.03,
        high_energy_burden_threshold=0.06,
    )
    assert "monthly_fixed_charge_increase" not in sweep.columns
    assert set(sweep["increase_kind"]) == {"percent"}
    rows = sweep[sweep["population"] == "All households"].sort_values("increase_amount")
    new_eb = rows["newly_energy_burdened_households"].to_numpy()
    assert new_eb[0] == pytest.approx(0.0)
    assert (np.diff(new_eb) >= -1e-9).all()
    assert new_eb[-1] == pytest.approx(20.0)


def test_sweep_fixed_keeps_legacy_column(households: pd.DataFrame) -> None:
    sweep = build_scenario_sweep(
        households, amounts=(0.0, 5.0), kind="fixed", apply_only_if_gas_positive=True
    )
    assert "monthly_fixed_charge_increase" in sweep.columns
    assert sweep["monthly_fixed_charge_increase"].tolist() == sweep["increase_amount"].tolist()
