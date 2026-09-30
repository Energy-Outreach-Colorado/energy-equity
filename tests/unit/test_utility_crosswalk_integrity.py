"""Integrity checks for the packaged utility territory crosswalks."""

from __future__ import annotations

import pandas as pd
import pytest

from energy_equity.calibration import PACKAGED_EIA176_RESOURCE, PACKAGED_EIA861_RESOURCE
from energy_equity.territory_calibration import load_packaged_crosswalk

EIA_TABLES = {
    "electric": (PACKAGED_EIA861_RESOURCE, "utility_number"),
    "gas": (PACKAGED_EIA176_RESOURCE, "company_id"),
}


@pytest.mark.parametrize("fuel", ["electric", "gas"])
def test_schema_and_unique_names(fuel: str) -> None:
    crosswalk = load_packaged_crosswalk(fuel, "CO")
    assert list(crosswalk.columns) == ["territory_name", "eia_id"]
    assert crosswalk["territory_name"].is_unique
    assert crosswalk["territory_name"].notna().all()
    assert crosswalk["eia_id"].notna().all()


@pytest.mark.parametrize("fuel", ["electric", "gas"])
def test_every_id_has_a_colorado_row_each_year(fuel: str) -> None:
    resource, id_col = EIA_TABLES[fuel]
    with resource.open("r", encoding="utf-8") as fh:
        eia = pd.read_csv(fh)
    colorado = eia[(eia["state"] == "CO") & (eia["customer_class"] == "residential")]
    ids = set(load_packaged_crosswalk(fuel, "CO")["eia_id"])
    for year, rows in colorado.groupby("year"):
        missing = sorted(ids - set(rows[id_col]))
        assert not missing, f"{fuel} crosswalk ids with no Colorado {year} row: {missing}"


def test_state_lookup_is_case_insensitive_and_strict() -> None:
    assert load_packaged_crosswalk("gas", "co").equals(load_packaged_crosswalk("gas", "CO"))
    with pytest.raises(ValueError, match="no packaged electric crosswalk"):
        load_packaged_crosswalk("electric", "WY")
    with pytest.raises(ValueError, match="fuel must be"):
        load_packaged_crosswalk("propane", "CO")
