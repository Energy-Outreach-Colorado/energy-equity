"""Tests for the LIHEAP SMI lookup and per-household 60% SMI flag."""

from __future__ import annotations

import pandas as pd
import pytest

from energy_equity.thresholds.smi import (
    attach_smi_statewide_threshold,
    load_packaged_smi_table,
    smi_lookup_for,
)


def test_packaged_smi_table_loads_colorado_fy2025() -> None:
    """The CO FY2025 row transcribed in liheap_smi_by_state_year.csv must round-trip."""
    df = load_packaged_smi_table()
    co_2025 = df[(df["state_fips"] == "08") & (df["fy"] == 2025)]
    assert len(co_2025) >= 10  # 1-10 person households at minimum
    # Spot check from the LIHEAP IM-2024-04 transcription.
    assert co_2025.loc[co_2025["hh_size"] == 4, "smi60_annual"].iloc[0] == 83256


def test_smi_lookup_for_colorado_fy2025() -> None:
    lookup = smi_lookup_for("08", "liheap_fy2025")
    assert lookup[1] == 43293
    assert lookup[4] == 83256
    assert lookup[10] == 119889


def test_smi_lookup_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="No SMI thresholds packaged"):
        smi_lookup_for("99", "liheap_fy2025")


def test_smi_lookup_rejects_malformed_source() -> None:
    with pytest.raises(ValueError, match="liheap_fy"):
        smi_lookup_for("08", "totally-not-a-source")


def test_attach_smi_statewide_threshold_flags() -> None:
    table = pd.DataFrame(
        {
            "state_fips": ["08"] * 3,
            "state_abbr": ["CO"] * 3,
            "fy": [2025] * 3,
            "hh_size": [1, 2, 3],
            "smi60_annual": [40000, 55000, 70000],
        }
    )
    df = pd.DataFrame(
        {
            "NP": [1, 2, 5],
            "income_adjusted": [30000.0, 60000.0, 100000.0],
        }
    )
    df = attach_smi_statewide_threshold(
        df, state_fips="08", smi_source="liheap_fy2025", table=table
    )
    # Row 0: 30k <= 40k -> eligible.
    assert bool(df["le_60_smi"].iloc[0]) is True
    # Row 1: 60k > 55k -> not eligible.
    assert bool(df["le_60_smi"].iloc[1]) is False
    # Row 2: NP=5 clips to max published size (3); 100k > 70k -> not eligible.
    assert df["hh_size_for_smi"].iloc[2] == 3
    assert bool(df["le_60_smi"].iloc[2]) is False
    assert df["smi_weight"].iloc[0] == pytest.approx(1.0)
    assert df["smi_weight"].iloc[1] == pytest.approx(0.0)
