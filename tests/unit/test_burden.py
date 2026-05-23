"""Tests for energy and rent burden calculations."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_equity.burden import (
    add_energy_component_fields,
    compute_energy_burden,
    compute_rent_burden,
)


@pytest.fixture
def households() -> pd.DataFrame:
    """Four households with varying income, energy use, and tenure."""
    return pd.DataFrame(
        {
            "ELEP": [100.0, 50.0, 0.0, float("nan")],  # monthly electric
            "GASP": [50.0, 25.0, 0.0, 30.0],  # monthly gas
            "FULP": [200.0, 0.0, 0.0, 0.0],  # annual other fuel
            "GRNTP": [1000.0, 0.0, 800.0, 0.0],  # monthly rent
            "TEN": [3, 1, 4, 1],  # 3=renter, 1=owner, 4=no-rent occupant
            "income_adjusted": [20000.0, 100000.0, -500.0, 30000.0],
        }
    )


def test_add_energy_component_fields_default_zero_rule(households: pd.DataFrame) -> None:
    add_energy_component_fields(households)
    # Row 0: 100*12 + 50*12 + 200 = 2000
    assert households["energy_cost_annual"].iloc[0] == pytest.approx(2000)
    # Row 3: NaN ELEP -> 0 under "zero" rule, so 0 + 30*12 + 0 = 360
    assert households["energy_cost_annual"].iloc[3] == pytest.approx(360)


def test_add_energy_component_fields_nan_rule(households: pd.DataFrame) -> None:
    add_energy_component_fields(households, missing_cost_rule="nan")
    # Row 3 has NaN ELEP -> NaN annual energy cost
    assert np.isnan(households["energy_cost_annual"].iloc[3])


def test_compute_energy_burden_basic(households: pd.DataFrame) -> None:
    add_energy_component_fields(households)
    compute_energy_burden(households, threshold=0.06, high_threshold=0.10)
    # Row 0: 2000/20000 = 0.10 -> burdened AND high
    assert households["energy_burden"].iloc[0] == pytest.approx(0.10)
    assert bool(households["energy_burdened"].iloc[0]) is True
    assert bool(households["high_energy_burdened"].iloc[0]) is True
    # Row 1: 900/100000 = 0.009 -> not burdened
    assert households["energy_burden"].iloc[1] == pytest.approx(0.009)
    assert bool(households["energy_burdened"].iloc[1]) is False


def test_compute_energy_burden_excludes_nonpos_income(households: pd.DataFrame) -> None:
    add_energy_component_fields(households)
    compute_energy_burden(households, nonpos_income_rule="exclude")
    # Row 2 has income=-500 -> NaN burden, both flags False
    assert np.isnan(households["energy_burden"].iloc[2])
    assert bool(households["energy_burdened"].iloc[2]) is False
    assert bool(households["high_energy_burdened"].iloc[2]) is False


def test_compute_energy_burden_treats_nonpos_as_high(households: pd.DataFrame) -> None:
    add_energy_component_fields(households)
    compute_energy_burden(households, nonpos_income_rule="treat_as_high")
    # Row 2 (income=-500) -> +inf burden, both flags True
    assert np.isinf(households["energy_burden"].iloc[2])
    assert bool(households["energy_burdened"].iloc[2]) is True
    assert bool(households["high_energy_burdened"].iloc[2]) is True


def test_compute_energy_burden_rejects_inverted_thresholds(households: pd.DataFrame) -> None:
    add_energy_component_fields(households)
    with pytest.raises(ValueError):
        compute_energy_burden(households, threshold=0.10, high_threshold=0.06)


def test_compute_rent_burden_renters_only(households: pd.DataFrame) -> None:
    compute_rent_burden(households, threshold=0.30, severe_threshold=0.50)
    # Row 0 (renter, $12k rent / $20k income = 0.60) -> severely rent burdened
    assert households["rent_burden"].iloc[0] == pytest.approx(0.60)
    assert bool(households["rent_burdened"].iloc[0]) is True
    assert bool(households["severely_rent_burdened"].iloc[0]) is True
    # Row 1 (owner) -> NaN burden, both flags False
    assert np.isnan(households["rent_burden"].iloc[1])
    assert bool(households["rent_burdened"].iloc[1]) is False


def test_compute_rent_burden_include_owners() -> None:
    df = pd.DataFrame(
        {
            "GRNTP": [500.0, 1000.0],
            "TEN": [1, 1],  # both owners
            "income_adjusted": [30000.0, 24000.0],
        }
    )
    compute_rent_burden(df, only_renters=False, tenure_col=None)
    assert df["rent_burden"].iloc[0] == pytest.approx(500 * 12 / 30000)
    assert df["rent_burden"].iloc[1] == pytest.approx(1000 * 12 / 24000)
    assert bool(df["severely_rent_burdened"].iloc[1]) is True
