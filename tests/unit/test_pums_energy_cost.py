"""Tests for PUMS income/energy-cost adjustment and the fixed-charge scenario."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_equity.pums.energy_cost import (
    apply_energy_cost_adjustment,
    apply_fixed_charge_scenario,
    apply_income_adjustment,
    compute_energy_burden_pums,
    compute_post_scenario_burden_and_deltas,
)


@pytest.fixture
def pums_rows() -> pd.DataFrame:
    """Three minimal PUMS-shaped rows.

    The ADJINC/ADJHSG factors below are integers that divide by 1_000_000 into clean
    floats so the arithmetic is easy to eyeball in the assertions.
    """
    return pd.DataFrame(
        {
            "HINCP": [50000.0, 100000.0, -500.0],
            "ADJINC": [1_000_000, 1_100_000, 1_000_000],
            "ADJHSG": [1_000_000, 1_100_000, 1_000_000],
            "ELEP": [100.0, 50.0, 0.0],
            "GASP": [50.0, 25.0, 0.0],
            "FULP": [200.0, 0.0, 0.0],
        }
    )


def test_income_adjustment(pums_rows: pd.DataFrame) -> None:
    apply_income_adjustment(pums_rows)
    # Row 0: 50000 * 1.0 = 50000. Row 1: 100000 * 1.1 = 110000.
    assert pums_rows["income_adjusted"].iloc[0] == pytest.approx(50000.0)
    assert pums_rows["income_adjusted"].iloc[1] == pytest.approx(110000.0)
    assert pums_rows["adjinc_factor"].iloc[0] == pytest.approx(1.0)
    assert pums_rows["adjhsg_factor"].iloc[1] == pytest.approx(1.1)


def test_energy_cost_adjustment_zero_rule(pums_rows: pd.DataFrame) -> None:
    apply_income_adjustment(pums_rows)
    apply_energy_cost_adjustment(pums_rows, missing_cost_rule="zero")
    # Row 0: 12 * (100 + 50) + 200 = 2000.
    assert pums_rows["annual_energy_cost_adj"].iloc[0] == pytest.approx(2000.0)
    # Row 1 with ADJHSG=1.1: 12 * (50*1.1 + 25*1.1) + 0*1.1 = 12 * 82.5 = 990.
    assert pums_rows["annual_energy_cost_adj"].iloc[1] == pytest.approx(990.0)


def test_compute_energy_burden_pums(pums_rows: pd.DataFrame) -> None:
    apply_income_adjustment(pums_rows)
    apply_energy_cost_adjustment(pums_rows)
    compute_energy_burden_pums(pums_rows, threshold=0.06, high_threshold=0.10)
    # Row 0: 2000 / 50000 = 0.04 -> not burdened.
    assert pums_rows["energy_burden"].iloc[0] == pytest.approx(0.04)
    assert bool(pums_rows["energy_burdened"].iloc[0]) is False
    # Row 2 has income <= 0 under default "exclude" rule -> NaN burden.
    assert np.isnan(pums_rows["energy_burden"].iloc[2])
    assert bool(pums_rows["burden_valid"].iloc[2]) is False


def test_fixed_charge_scenario_only_if_gas_positive(pums_rows: pd.DataFrame) -> None:
    apply_income_adjustment(pums_rows)
    apply_energy_cost_adjustment(pums_rows)
    apply_fixed_charge_scenario(pums_rows, monthly_increase=5.0, apply_only_if_gas_positive=True)
    # Row 0 has GASP=50 (positive) -> annual_post = 2000 + 12*5 = 2060.
    assert pums_rows["annual_energy_cost_adj_post"].iloc[0] == pytest.approx(2060.0)
    # Row 2 has GASP=0 -> annual_post == base.
    assert pums_rows["annual_energy_cost_adj_post"].iloc[2] == pytest.approx(
        pums_rows["annual_energy_cost_adj"].iloc[2]
    )


def test_fixed_charge_scenario_universal_application(pums_rows: pd.DataFrame) -> None:
    apply_income_adjustment(pums_rows)
    apply_energy_cost_adjustment(pums_rows)
    apply_fixed_charge_scenario(pums_rows, monthly_increase=5.0, apply_only_if_gas_positive=False)
    # Even row 2 (GASP=0) gets the +60 annual increment.
    diff = pums_rows["annual_energy_cost_adj_post"] - pums_rows["annual_energy_cost_adj"]
    np.testing.assert_allclose(diff.to_numpy(), 60.0)


def test_post_scenario_deltas(pums_rows: pd.DataFrame) -> None:
    apply_income_adjustment(pums_rows)
    apply_energy_cost_adjustment(pums_rows)
    compute_energy_burden_pums(pums_rows, threshold=0.06)
    apply_fixed_charge_scenario(pums_rows, monthly_increase=200.0)
    compute_post_scenario_burden_and_deltas(
        pums_rows,
        threshold=0.06,
        high_threshold=0.10,
        delta_thresholds_pp=(0.25, 0.50, 1.00),
    )
    # Row 0: cost 2000 + 12*200=2400 -> post=4400; burden=0.04 -> 0.088 -> newly burdened.
    assert bool(pums_rows["newly_energy_burdened"].iloc[0]) is True
    assert pums_rows["delta_energy_burden"].iloc[0] == pytest.approx(2400 / 50000)
    assert bool(pums_rows["delta_burden_ge_1_00pp"].iloc[0]) is True
    # Row 2 had no valid base burden -> no delta.
    assert np.isnan(pums_rows["delta_energy_burden"].iloc[2])
