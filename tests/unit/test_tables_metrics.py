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
