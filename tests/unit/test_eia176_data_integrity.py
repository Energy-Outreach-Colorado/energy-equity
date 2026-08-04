"""Integrity checks for the packaged EIA-176 residential gas table."""

from __future__ import annotations

import pandas as pd
import pytest

from energy_equity.calibration import PACKAGED_EIA176_RESOURCE

EXPECTED_YEARS = {2022, 2023, 2024}


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    with PACKAGED_EIA176_RESOURCE.open("r", encoding="utf-8") as fh:
        return pd.read_csv(fh)


def test_schema(table: pd.DataFrame) -> None:
    expected = [
        "company_id",
        "company_name",
        "state",
        "customer_class",
        "revenue_thousand_dollars",
        "volume_mcf",
        "customers",
        "ownership",
        "year",
    ]
    assert list(table.columns) == expected
    assert (table["customer_class"] == "residential").all()
    assert set(table["year"].unique()) == EXPECTED_YEARS


def test_key_uniqueness(table: pd.DataFrame) -> None:
    assert not table.duplicated(subset=["year", "company_id", "state"]).any()


def test_values_plausible(table: pd.DataFrame) -> None:
    assert (table["customers"] > 0).all()
    assert (table["revenue_thousand_dollars"] >= 0).all()
    for year, grp in table.groupby("year"):
        assert 1000 <= len(grp) <= 1600, f"unexpected company count for {year}"
        national_customers = grp["customers"].sum()
        assert 50_000_000 <= national_customers <= 90_000_000
        national_avg = grp["revenue_thousand_dollars"].sum() * 1000 / national_customers
        assert 500 <= national_avg <= 1_500, f"national avg gas bill {national_avg} for {year}"


def test_psco_reference_row(table: pd.DataFrame) -> None:
    psco = table[(table["company_id"] == 17611459) & (table["year"] == 2023)]
    assert len(psco) == 1
    row = psco.iloc[0]
    assert row["state"] == "CO"
    avg_bill = row["revenue_thousand_dollars"] * 1000 / row["customers"]
    assert 600 <= avg_bill <= 1_100
