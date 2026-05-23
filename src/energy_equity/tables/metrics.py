"""Canonical metric definitions for state and PUMA tables.

Splitting the metric registry out of the table builder keeps the aggregation engine
generic. Pipelines pick the set they need (baseline only, baseline + fixed-charge,
etc.) and pass it into `tables.core.build_table`.

Convention: a `CountMetric` mask returns a 0/1 (or fractional) float array of length N
matching `microdata.df`. AMI's `ami_weight` is fractional under the
expected-probability method; everything else is binary.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import CountMetric, RatioMetric


def _bool_col(df: pd.DataFrame, col: str) -> np.ndarray:
    """0/1 mask from a boolean column; missing column returns all-zero mask."""
    if col not in df.columns:
        return np.zeros(len(df), dtype=float)
    return df[col].fillna(False).astype(float).to_numpy()


def _float_col(df: pd.DataFrame, col: str, default: float = 0.0) -> np.ndarray:
    """Float mask from a numeric column; missing column returns `default`-filled mask."""
    if col not in df.columns:
        return np.full(len(df), default, dtype=float)
    return df[col].astype(float).fillna(default).to_numpy()


# -----------------------------------------------------------------------------
# Baseline metrics — energy burden, AMI <=80%, SMI <=60%, rent burden, intersections.
# -----------------------------------------------------------------------------


BASELINE_COUNT_METRICS: tuple[CountMetric, ...] = (
    CountMetric("hh_total", lambda df: np.ones(len(df), dtype=float)),
    CountMetric("hh_burden_valid", lambda df: _bool_col(df, "burden_valid")),
    CountMetric("hh_eb", lambda df: _bool_col(df, "energy_burdened")),
    CountMetric("hh_heb", lambda df: _bool_col(df, "high_energy_burdened")),
    # AMI <=80%. Uses ami_weight so the same metric handles binary and probabilistic flags.
    CountMetric(
        "hh_ami_valid", lambda df: (~pd.isna(df.get("ami_weight"))).astype(float).to_numpy()
    ),
    CountMetric("hh_le80", lambda df: _float_col(df, "ami_weight")),
    CountMetric(
        "hh_eb_le80",
        lambda df: _float_col(df, "ami_weight") * _bool_col(df, "energy_burdened"),
    ),
    CountMetric(
        "hh_heb_le80",
        lambda df: _float_col(df, "ami_weight") * _bool_col(df, "high_energy_burdened"),
    ),
    # SMI <=60%. Uses smi_weight (binary).
    CountMetric(
        "hh_smi_valid", lambda df: (~pd.isna(df.get("smi_weight"))).astype(float).to_numpy()
    ),
    CountMetric("hh_le60_smi", lambda df: _float_col(df, "smi_weight")),
    CountMetric(
        "hh_eb_le60_smi",
        lambda df: _float_col(df, "smi_weight") * _bool_col(df, "energy_burdened"),
    ),
    CountMetric(
        "hh_heb_le60_smi",
        lambda df: _float_col(df, "smi_weight") * _bool_col(df, "high_energy_burdened"),
    ),
    # Rent burden (renters only).
    CountMetric(
        "hh_renter",
        lambda df: (
            (df.get("TEN", pd.Series([0])) == 3).astype(float).to_numpy()
            if "TEN" in df.columns
            else np.zeros(len(df), dtype=float)
        ),
    ),
    CountMetric("hh_rent_valid", lambda df: _bool_col(df, "rent_valid")),
    CountMetric("hh_rent_burdened", lambda df: _bool_col(df, "rent_burdened")),
    CountMetric("hh_severe_rent_burdened", lambda df: _bool_col(df, "severe_rent_burdened")),
    # Intersections — used heavily in the persuasive reports.
    CountMetric(
        "hh_rent_burdened_le80",
        lambda df: _float_col(df, "ami_weight") * _bool_col(df, "rent_burdened"),
    ),
    CountMetric(
        "hh_rent_burdened_le60_smi",
        lambda df: _float_col(df, "smi_weight") * _bool_col(df, "rent_burdened"),
    ),
    CountMetric(
        "hh_eb_and_rent_burdened",
        lambda df: _bool_col(df, "energy_burdened") * _bool_col(df, "rent_burdened"),
    ),
    CountMetric(
        "hh_heb_and_rent_burdened",
        lambda df: _bool_col(df, "high_energy_burdened") * _bool_col(df, "rent_burdened"),
    ),
    CountMetric(
        "hh_eb_and_rent_burdened_le80",
        lambda df: (
            _float_col(df, "ami_weight")
            * _bool_col(df, "energy_burdened")
            * _bool_col(df, "rent_burdened")
        ),
    ),
    CountMetric(
        "hh_eb_and_rent_burdened_le60_smi",
        lambda df: (
            _float_col(df, "smi_weight")
            * _bool_col(df, "energy_burdened")
            * _bool_col(df, "rent_burdened")
        ),
    ),
)


BASELINE_RATIO_METRICS: tuple[RatioMetric, ...] = (
    RatioMetric("pct_energy_burdened_of_valid", "hh_eb", "hh_burden_valid"),
    RatioMetric("pct_high_burdened_of_valid", "hh_heb", "hh_burden_valid"),
    RatioMetric("pct_le80_of_total", "hh_le80", "hh_total"),
    RatioMetric("pct_eb_among_le80", "hh_eb_le80", "hh_le80"),
    RatioMetric("pct_heb_among_le80", "hh_heb_le80", "hh_le80"),
    RatioMetric("pct_le60_smi_of_total", "hh_le60_smi", "hh_total"),
    RatioMetric("pct_eb_among_le60_smi", "hh_eb_le60_smi", "hh_le60_smi"),
    RatioMetric("pct_heb_among_le60_smi", "hh_heb_le60_smi", "hh_le60_smi"),
    RatioMetric("pct_rent_burdened_of_rent_valid", "hh_rent_burdened", "hh_rent_valid"),
    RatioMetric(
        "pct_severe_rent_burdened_of_rent_valid",
        "hh_severe_rent_burdened",
        "hh_rent_valid",
    ),
    RatioMetric("pct_rent_burdened_among_le80", "hh_rent_burdened_le80", "hh_le80"),
    RatioMetric("pct_rent_burdened_among_le60_smi", "hh_rent_burdened_le60_smi", "hh_le60_smi"),
)
