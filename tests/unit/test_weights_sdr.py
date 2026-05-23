"""Tests for SDR variance and 90% margin of error."""

from __future__ import annotations

import numpy as np
import pytest

from energy_equity.weights.sdr import (
    PUMS_REPLICATE_COUNT,
    PUMS_REPLICATE_FACTOR,
    Z_90,
    replicate_metric_cols,
    sdr_moe,
    sdr_moe_scalar,
    sdr_variance,
)


def test_pums_factor_is_4_over_80() -> None:
    assert pytest.approx(0.05) == PUMS_REPLICATE_FACTOR
    assert PUMS_REPLICATE_COUNT == 80


def test_zero_variance_when_replicates_equal_point() -> None:
    point = np.array([100.0, 200.0])
    reps = np.tile(point[:, None], (1, 80))
    assert np.allclose(sdr_variance(point, reps), 0.0)
    assert np.allclose(sdr_moe(point, reps), 0.0)


def test_variance_matches_manual_formula() -> None:
    point = np.array([10.0])
    reps = np.array([[12.0, 8.0, 10.0, 10.0]])
    factor = 4 / 4
    expected = factor * ((12 - 10) ** 2 + (8 - 10) ** 2 + (10 - 10) ** 2 + (10 - 10) ** 2)
    assert sdr_variance(point, reps, factor=factor)[0] == pytest.approx(expected)


def test_moe_uses_90pct_z_by_default() -> None:
    point = np.array([0.0])
    reps = np.array([[1.0] * 80])
    var = sdr_variance(point, reps)[0]
    expected = Z_90 * np.sqrt(var)
    assert sdr_moe(point, reps)[0] == pytest.approx(expected)


def test_sdr_moe_scalar_matches_array_path() -> None:
    rng = np.random.default_rng(seed=42)
    reps = rng.normal(size=80)
    expected = sdr_moe(np.array([0.0]), reps[np.newaxis, :])[0]
    assert sdr_moe_scalar(0.0, reps) == pytest.approx(expected)


def test_mismatched_shapes_raises() -> None:
    with pytest.raises(ValueError):
        sdr_variance(np.array([1.0, 2.0]), np.array([[1.0, 2.0]]))


def test_replicate_metric_cols_default_length() -> None:
    cols = replicate_metric_cols("hh_total_w")
    assert len(cols) == 80
    assert cols[0] == "hh_total_w_rep1"
    assert cols[-1] == "hh_total_w_rep80"


def test_replicate_metric_cols_custom_length() -> None:
    assert replicate_metric_cols("x", 3) == ["x_rep1", "x_rep2", "x_rep3"]


def test_negative_variance_clamped_to_zero_under_sqrt() -> None:
    """Defense in depth: ensure sqrt does not yield NaN if floating-point makes variance < 0."""
    point = np.array([0.0])
    reps = np.zeros((1, 80))
    moe = sdr_moe(point, reps)
    assert not np.isnan(moe).any()
    assert moe[0] == pytest.approx(0.0)
