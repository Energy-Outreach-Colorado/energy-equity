"""Integrity checks for the packaged EIA-861 residential table.

Guards against a bad rebuild of data/eia861/eia861_residential.csv: schema drift,
duplicate keys, excluded sentinel rows sneaking back in, and implausible derived
average bills.
"""

from __future__ import annotations

import pandas as pd
import pytest

from energy_equity.calibration import PACKAGED_EIA861_RESOURCE

EXPECTED_YEARS = {2023, 2024}


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    with PACKAGED_EIA861_RESOURCE.open("r", encoding="utf-8") as fh:
        return pd.read_csv(fh)


def test_schema(table: pd.DataFrame) -> None:
    expected = [
        "utility_number",
        "utility_name",
        "state",
        "customer_class",
        "revenue_thousand_dollars",
        "sales_mwh",
        "customers",
        "ownership",
        "year",
    ]
    assert list(table.columns) == expected
    assert (table["customer_class"] == "residential").all()
    assert set(table["year"].unique()) == EXPECTED_YEARS


def test_key_uniqueness(table: pd.DataFrame) -> None:
    assert not table.duplicated(subset=["year", "utility_number", "state"]).any()


def test_excluded_rows_absent(table: pd.DataFrame) -> None:
    assert not table["utility_number"].isin([88888, 99999]).any()
    assert not table["ownership"].str.strip().str.lower().eq("behind the meter").any()


def test_values_plausible(table: pd.DataFrame) -> None:
    assert (table["customers"] > 0).all()
    assert (table["revenue_thousand_dollars"] >= 0).all()
    for year, grp in table.groupby("year"):
        assert 1500 <= len(grp) <= 2500, f"unexpected utility count for {year}"
        national_customers = grp["customers"].sum()
        assert 100_000_000 <= national_customers <= 200_000_000
        national_avg = grp["revenue_thousand_dollars"].sum() * 1000 / national_customers
        assert 1_000 <= national_avg <= 2_500, f"national avg bill {national_avg} for {year}"


def test_psco_reference_row(table: pd.DataFrame) -> None:
    psco = table[(table["utility_number"] == 15466) & (table["year"] == 2024)]
    assert len(psco) == 1
    row = psco.iloc[0]
    assert row["state"] == "CO"
    avg_bill = row["revenue_thousand_dollars"] * 1000 / row["customers"]
    assert 900 <= avg_bill <= 1400
