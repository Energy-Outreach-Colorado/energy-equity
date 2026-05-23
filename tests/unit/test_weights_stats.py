"""Tests for weighted descriptive statistics and the count/rate estimator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_equity.weights.stats import (
    estimate_count_and_rate,
    weighted_mean,
    weighted_median,
    weighted_quantile,
    weighted_total,
)


def test_weighted_total_simple() -> None:
    assert weighted_total([1, 2, 3], [1, 1, 1]) == pytest.approx(6)
    assert weighted_total([10, 20], [0.5, 1.5]) == pytest.approx(10 * 0.5 + 20 * 1.5)


def test_weighted_total_ignores_nan_by_default() -> None:
    v = [1.0, float("nan"), 3.0]
    w = [1.0, 5.0, 2.0]
    assert weighted_total(v, w) == pytest.approx(1 * 1 + 3 * 2)


def test_weighted_total_propagates_nan_when_asked() -> None:
    v = [1.0, float("nan"), 3.0]
    w = [1.0, 1.0, 1.0]
    out = weighted_total(v, w, ignore_nan=False)
    assert np.isnan(out)


def test_weighted_mean_simple() -> None:
    assert weighted_mean([1, 2, 3, 4], [1, 1, 1, 1]) == pytest.approx(2.5)
    assert weighted_mean([10, 30], [3, 1]) == pytest.approx((10 * 3 + 30 * 1) / 4)


def test_weighted_mean_zero_weight_returns_nan() -> None:
    assert np.isnan(weighted_mean([1, 2], [0, 0]))


def test_weighted_quantile_uniform_weights_matches_hazen() -> None:
    """With uniform weights, weighted_quantile uses cumulative-midpoint (Hazen / Type-5).

    Numpy exposes this method explicitly as `method="hazen"`. The package default-quantile
    convention matches it so weighted and unweighted quantiles agree on uniform samples.
    """
    rng = np.random.default_rng(seed=1)
    values = rng.normal(size=200)
    weights = np.ones_like(values)
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        expected = np.quantile(values, q, method="hazen")
        assert weighted_quantile(values, weights, q) == pytest.approx(expected, abs=1e-9)


def test_weighted_median_skewed_weights_pulls_toward_heavy_mass() -> None:
    """A heavy weight on the small value pulls the median toward it (and vice versa)."""
    values = [0.0, 100.0]
    # 99% of mass at 0 -> median ~= 1 (interpolated between 0 and 100 at cum-midpoint).
    assert weighted_median(values, [99.0, 1.0]) <= 1.0 + 1e-9
    # 99% of mass at 100 -> median ~= 99.
    assert weighted_median(values, [1.0, 99.0]) >= 99.0 - 1e-9


def test_weighted_quantile_endpoints() -> None:
    """q=0 and q=1 return the min and max values regardless of weights."""
    assert weighted_quantile([1.0, 5.0, 9.0], [2.0, 3.0, 1.0], 0.0) == pytest.approx(1.0)
    assert weighted_quantile([1.0, 5.0, 9.0], [2.0, 3.0, 1.0], 1.0) == pytest.approx(9.0)


def test_weighted_quantile_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        weighted_quantile([1, 2, 3], [1, 1, 1], 1.1)


def test_estimate_count_and_rate_no_replicates() -> None:
    df = pd.DataFrame(
        {
            "WGTP": [10.0, 20.0, 30.0, 40.0],
            "energy_burdened": [True, False, True, False],
        }
    )
    result = estimate_count_and_rate(df, df["energy_burdened"].to_numpy(), weight_col="WGTP")
    assert result["count"] == pytest.approx(40.0)
    assert result["universe"] == pytest.approx(100.0)
    assert result["rate"] == pytest.approx(0.4)
    assert result["unweighted_n"] == pytest.approx(2)
    assert np.isnan(result["count_moe"])
    assert np.isnan(result["rate_moe"])


def test_estimate_count_and_rate_with_replicates() -> None:
    """With identical replicates to the point estimate, MOEs should be zero."""
    n = 4
    reps = [f"rep{i}" for i in range(80)]
    df = pd.DataFrame({"WGTP": [10.0, 20.0, 30.0, 40.0]})
    for r in reps:
        df[r] = df["WGTP"]
    mask = np.array([True, False, True, False])
    result = estimate_count_and_rate(df, mask, weight_col="WGTP", replicate_weight_cols=reps)
    assert result["count_moe"] == pytest.approx(0.0)
    assert result["universe_moe"] == pytest.approx(0.0)
    assert result["rate_moe"] == pytest.approx(0.0)


def test_estimate_count_and_rate_respects_universe_mask() -> None:
    df = pd.DataFrame({"WGTP": [10.0, 20.0, 30.0, 40.0]})
    mask = np.array([True, False, True, False])
    universe = np.array([True, True, True, False])  # exclude 4th row from denominator
    result = estimate_count_and_rate(df, mask, weight_col="WGTP", universe_mask=universe)
    assert result["count"] == pytest.approx(40.0)
    assert result["universe"] == pytest.approx(60.0)
    assert result["rate"] == pytest.approx(40 / 60)


def test_estimate_count_and_rate_zero_universe() -> None:
    df = pd.DataFrame({"WGTP": [0.0, 0.0]})
    mask = np.array([True, True])
    result = estimate_count_and_rate(df, mask, weight_col="WGTP")
    assert result["universe"] == 0.0
    assert np.isnan(result["rate"])
