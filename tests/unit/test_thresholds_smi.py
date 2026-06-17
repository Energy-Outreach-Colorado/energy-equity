"""Tests for the LIHEAP SMI lookup, year resolution, and per-household 60% SMI flag."""

from __future__ import annotations

import pandas as pd
import pytest

from energy_equity.thresholds.smi import (
    attach_smi_statewide_threshold,
    load_packaged_smi_table,
    resolve_smi_fiscal_year,
    smi_lookup_for,
)


def test_packaged_smi_table_covers_all_states_and_three_years() -> None:
    df = load_packaged_smi_table()
    assert df["state_fips"].nunique() == 52  # 50 states + DC + PR
    assert sorted(int(y) for y in df["fy"].unique()) == [2025, 2026, 2027]
    assert sorted(int(s) for s in df["hh_size"].unique()) == list(range(1, 13))
    # Colorado size-4 differs per IM issuance (the point of per-year sourcing).
    co25 = df[(df["state_fips"] == "08") & (df["fy"] == 2025)].set_index("hh_size")["smi60_annual"]
    co26 = df[(df["state_fips"] == "08") & (df["fy"] == 2026)].set_index("hh_size")["smi60_annual"]
    co27 = df[(df["state_fips"] == "08") & (df["fy"] == 2027)].set_index("hh_size")["smi60_annual"]
    assert co25.loc[4] == 78062  # IM2024-02
    assert co26.loc[4] == 83256  # IM2025-02
    assert co27.loc[4] == 87106  # FY2027 Attachment 4


def test_smi_lookup_for_multiple_states() -> None:
    table = load_packaged_smi_table()
    co = smi_lookup_for("08", "liheap_fy2026", anchor_year=2026, table=table)
    assert co[1] == 43293 and co[4] == 83256 and co[12] == 124884
    tx = smi_lookup_for("48", "liheap_fy2027", anchor_year=2027, table=table)
    assert tx[1] == 35018 and tx[6] == 88892


def test_smi_auto_resolves_to_closest_not_after_anchor() -> None:
    table = load_packaged_smi_table()
    assert resolve_smi_fiscal_year("08", "auto", anchor_year=2027, table=table) == 2027
    assert resolve_smi_fiscal_year("08", "auto", anchor_year=2026, table=table) == 2026
    assert resolve_smi_fiscal_year("08", "auto", anchor_year=2025, table=table) == 2025
    # Anchor before any packaged year -> earliest available (with a warning).
    assert resolve_smi_fiscal_year("08", "auto", anchor_year=2020, table=table) == 2025
    # Anchor after the latest -> latest <= anchor.
    assert resolve_smi_fiscal_year("08", "auto", anchor_year=2030, table=table) == 2027


def test_smi_explicit_year_divergence_warns(caplog: pytest.LogCaptureFixture) -> None:
    table = load_packaged_smi_table()
    # |2026 - 2024| = 2 > 1 -> warn (but still resolves to 2026).
    from loguru import logger

    handler_id = logger.add(caplog.handler, level="WARNING")
    logger.enable("energy_equity")
    try:
        fy = resolve_smi_fiscal_year("08", "liheap_fy2026", anchor_year=2024, table=table)
    finally:
        logger.remove(handler_id)
    assert fy == 2026
    assert any("diverge" in r.message for r in caplog.records)


def test_smi_lookup_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="No SMI thresholds"):
        smi_lookup_for("99", "auto", anchor_year=2026)


def test_smi_lookup_rejects_malformed_source() -> None:
    table = load_packaged_smi_table()
    with pytest.raises(ValueError, match="auto.*or.*liheap_fy"):
        smi_lookup_for("08", "totally-not-a-source", anchor_year=2026, table=table)


def test_smi_csv_override(tmp_path) -> None:
    custom = tmp_path / "smi.csv"
    pd.DataFrame(
        {
            "state_fips": ["99"],
            "state_abbr": ["ZZ"],
            "fy": [2026],
            "hh_size": [1],
            "smi60_annual": [12345],
        }
    ).to_csv(custom, index=False)
    df = load_packaged_smi_table(custom)
    assert smi_lookup_for("99", "auto", anchor_year=2026, table=df)[1] == 12345


def test_attach_smi_statewide_threshold_flags() -> None:
    table = pd.DataFrame(
        {
            "state_fips": ["08"] * 3,
            "state_abbr": ["CO"] * 3,
            "fy": [2026] * 3,
            "hh_size": [1, 2, 3],
            "smi60_annual": [40000, 55000, 70000],
        }
    )
    df = pd.DataFrame({"NP": [1, 2, 5], "income_adjusted": [30000.0, 60000.0, 100000.0]})
    df = attach_smi_statewide_threshold(
        df, state_fips="08", smi_source="auto", anchor_year=2026, table=table
    )
    assert bool(df["le_60_smi"].iloc[0]) is True  # 30k <= 40k
    assert bool(df["le_60_smi"].iloc[1]) is False  # 60k > 55k
    assert df["hh_size_for_smi"].iloc[2] == 3  # NP=5 clipped to max size 3
    assert bool(df["le_60_smi"].iloc[2]) is False
    assert df["smi_weight"].iloc[0] == pytest.approx(1.0)
    assert df["smi_weight"].iloc[1] == pytest.approx(0.0)
