"""Tests for the reporting income-distribution builders."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_equity.reporting.income import (
    B19001_BINS,
    IncomeDistribution,
    aggregate_b19001,
    build_income_distribution_comparison,
    build_regressivity_table,
)
from energy_equity.reporting.metrics import bin_midpoint, safe_div, share_below_cutoff


def test_aggregate_b19001_sums_unweighted() -> None:
    cols = [c[0] for c in B19001_BINS]
    df = pd.DataFrame({c: [10, 20, 30] for c in cols} | {"tract_geoid": ["a", "b", "c"]})
    result = aggregate_b19001(df, weight_col=None)
    assert result.counts[cols[0]] == 60
    assert result.total == 60 * len(cols)


def test_aggregate_b19001_weighted() -> None:
    cols = [c[0] for c in B19001_BINS]
    df = pd.DataFrame(
        {c: [10, 20, 30] for c in cols} | {"tract_geoid": ["a", "b", "c"], "share": [1.0, 0.5, 0.0]}
    )
    result = aggregate_b19001(df, weight_col="share")
    # First column: 10*1 + 20*0.5 + 30*0 = 20
    assert result.counts[cols[0]] == pytest.approx(20)


def test_build_income_distribution_comparison() -> None:
    s = IncomeDistribution(counts={c[0]: 1.0 for c in B19001_BINS}, total=16.0)
    st = IncomeDistribution(counts={c[0]: 2.0 for c in B19001_BINS}, total=32.0)
    out = build_income_distribution_comparison(s, st)
    assert len(out) == 16
    # Equal shares -> diff_pp = 0 for every bin.
    np.testing.assert_allclose(out["share_diff_pp"].to_numpy(), 0.0, atol=1e-9)


def test_share_below_cutoff_uniform_bins() -> None:
    counts = pd.Series([100.0, 100.0, 100.0])  # 300 total
    uppers = pd.Series([10_000.0, 20_000.0, 30_000.0])
    assert share_below_cutoff(counts, uppers, 10_000) == pytest.approx(1 / 3)
    assert share_below_cutoff(counts, uppers, 20_000) == pytest.approx(2 / 3)
    # Mid-bin interpolation: 15_000 sits in bin (10k, 20k] at 50% -> 1/3 + 0.5 * 1/3 = 0.5.
    assert share_below_cutoff(counts, uppers, 15_000) == pytest.approx(0.5)


def test_build_regressivity_table_inverse_with_income() -> None:
    tab = build_regressivity_table(annual_fixed_charge_increase=100.0)
    # First (lowest) bin has the highest share-of-income.
    shares = tab["increase_as_share_of_income"].to_numpy()
    finite = shares[~np.isnan(shares)]
    assert finite[0] > finite[-1]


def test_bin_midpoint_open_top() -> None:
    assert bin_midpoint(200_000, None, fallback_for_open_top=300_000) == 300_000
    assert bin_midpoint(0, 10_000) == 5_000


def test_safe_div_zero_denominator() -> None:
    assert np.isnan(safe_div(5.0, 0))
    assert np.isnan(safe_div(5.0, float("nan")))
    assert safe_div(10, 4) == 2.5
