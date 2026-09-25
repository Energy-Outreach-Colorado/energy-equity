"""Tests for the metric registry — especially robustness to missing weight columns."""

from __future__ import annotations

import numpy as np
import pandas as pd

from energy_equity.pums.prepare import HouseholdMicrodata
from energy_equity.tables.core import build_table
from energy_equity.tables.metrics import (
    BASELINE_COUNT_METRICS,
    BASELINE_RATIO_METRICS,
    _valid_col,
)


def test_valid_col_missing_column_is_all_zeros() -> None:
    df = pd.DataFrame({"x": [1.0, 2.0]})
    np.testing.assert_array_equal(_valid_col(df, "absent"), np.zeros(2))


def test_valid_col_present_marks_nonnull() -> None:
    df = pd.DataFrame({"ami_weight": [1.0, np.nan, 0.0]})
    np.testing.assert_array_equal(_valid_col(df, "ami_weight"), np.array([1.0, 0.0, 1.0]))


def test_build_table_without_ami_or_smi_columns_does_not_crash() -> None:
    """Regression: hh_ami_valid / hh_smi_valid must not blow up when the weight columns
    are absent (previously `~pd.isna(df.get(col))` produced an int and crashed)."""
    df = pd.DataFrame(
        {
            "PUMA": ["00800", "00800", "00900"],
            "WGTP": [10.0, 20.0, 30.0],
            "burden_valid": [True, True, False],
            "energy_burdened": [True, False, False],
            "high_energy_burdened": [False, False, False],
            # NOTE: deliberately NO ami_weight / smi_weight / rent columns.
        }
    )
    md = HouseholdMicrodata(df=df, point_weight_col="WGTP", replicate_weight_cols=[])

    out = build_table(
        md,
        ["PUMA"],
        count_metrics=BASELINE_COUNT_METRICS,
        ratio_metrics=BASELINE_RATIO_METRICS,
        compute_moe=False,
        suppress_small_n=False,
    )

    # Absent weight columns -> the "valid" counts are zero, not an error.
    assert out["hh_ami_valid_w"].sum() == 0.0
    assert out["hh_smi_valid_w"].sum() == 0.0
    assert out["hh_le80_w"].sum() == 0.0
    # Sanity: the always-present metrics still aggregate.
    assert out["hh_total_w"].sum() == 60.0
    assert out["hh_eb_w"].sum() == 10.0


def test_energy_burden_bands_partition_valid_households() -> None:
    from energy_equity.tables.metrics import (
        ENERGY_BURDEN_BAND_COUNT_METRICS,
        energy_burden_band_name,
    )

    df = pd.DataFrame(
        {
            "PUMA": ["00800"] * 7,
            "WGTP": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
            "energy_burden": [0.01, 0.02, 0.059, 0.06, 0.25, np.inf, np.nan],
            "burden_valid": [True, True, True, True, True, True, False],
            "energy_burdened": [False, False, False, True, True, True, False],
            "high_energy_burdened": [False, False, False, False, True, True, False],
        }
    )
    md = HouseholdMicrodata(df=df, point_weight_col="WGTP", replicate_weight_cols=[])
    out = build_table(
        md,
        ["PUMA"],
        count_metrics=(*BASELINE_COUNT_METRICS, *ENERGY_BURDEN_BAND_COUNT_METRICS),
        compute_moe=False,
        suppress_small_n=False,
    ).iloc[0]

    assert energy_burden_band_name(0.06, 0.10) == "hh_eb_band_06_10"
    assert energy_burden_band_name(0.20, float("inf")) == "hh_eb_band_20_plus"
    assert out["hh_eb_band_00_02_w"] == 1.0
    assert out["hh_eb_band_02_04_w"] == 2.0
    assert out["hh_eb_band_04_06_w"] == 3.0
    assert out["hh_eb_band_06_10_w"] == 4.0
    assert out["hh_eb_band_10_20_w"] == 0.0
    assert out["hh_eb_band_20_plus_w"] == 11.0
    band_cols = [f"{m.name}_w" for m in ENERGY_BURDEN_BAND_COUNT_METRICS]
    assert out[band_cols].sum() == out["hh_burden_valid_w"]
    assert (
        out[["hh_eb_band_06_10_w", "hh_eb_band_10_20_w", "hh_eb_band_20_plus_w"]].sum()
        == (out["hh_eb_w"])
    )


def test_energy_cost_totals_sum_valid_households_only() -> None:
    from energy_equity.tables.metrics import ENERGY_COST_TOTAL_METRICS

    df = pd.DataFrame(
        {
            "PUMA": ["00800"] * 3,
            "WGTP": [2.0, 3.0, 5.0],
            "annual_energy_cost_adj": [1000.0, 2000.0, 9999.0],
            "income_adjusted": [50000.0, 20000.0, 0.0],
            "burden_valid": [True, True, False],
        }
    )
    md = HouseholdMicrodata(df=df, point_weight_col="WGTP", replicate_weight_cols=[])
    out = build_table(
        md,
        ["PUMA"],
        count_metrics=ENERGY_COST_TOTAL_METRICS,
        compute_moe=False,
        suppress_small_n=False,
    ).iloc[0]
    assert out["usd_energy_cost_valid_w"] == 2.0 * 1000 + 3.0 * 2000
    assert out["usd_income_valid_w"] == 2.0 * 50000 + 3.0 * 20000
