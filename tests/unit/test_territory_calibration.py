"""Unit tests for energy_equity.territory_calibration (per-utility factors blended by PUMA).

The fixture has four PUMAs of ten-weight households. Electric territory 1 (investor
owned, target $600) covers PUMAs 00100 and 00200, territory 2 (cooperative, target $900)
covers 00300, and territory 3 (cooperative) covers a tenth of 00200, too little to
estimate its own factor. Gas company 10 (target $360) covers 00100 and 00200. PUMA 00400
has no territory. Household D pays gas through its electric bill (GASFP 2) and household
E reports no costs.

Hand-computed factors. Territory 1 observes electric payers A, B and C (D excluded) at
(1200 + 1200 + 2400) / 3 = 1600, so 600 / 1600 = 0.375. Territory 2 observes F at 1800,
so 0.5. Territory 3 pools with the cooperative class at 0.5. The gas factor is
360 / 600 = 0.6. Territory 1's combined target is 600 + 360 = 960 against D's 3600, so
0.2667, and territory 2 has no combined-bill households and pools to that value.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energy_equity.territory_calibration import (
    Territory,
    apply_territory_calibration,
    blend_territory_values,
    build_territories,
    estimate_territory_factors,
    territory_overlap,
)


def shares(values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"PUMA": list(values), "share_households_in_service": list(values.values())}
    )


def microdata() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "SERIALNO": list("ABCDEFG"),
            "PUMA": ["00100", "00100", "00200", "00200", "00200", "00300", "00400"],
            "WGTP": [10.0] * 7,
            "ELEP": [100.0, 100.0, 200.0, 300.0, np.nan, 150.0, 100.0],
            "GASP": [50.0, np.nan, 50.0, np.nan, np.nan, np.nan, np.nan],
            "GASFP": [4, 3, 4, 2, 3, 3, 3],
        }
    )
    df["annual_electric_cost_adj"] = 12.0 * df["ELEP"].fillna(0.0)
    df["annual_gas_cost_adj"] = 12.0 * df["GASP"].fillna(0.0)
    df["annual_other_fuel_cost_adj"] = 0.0
    df["annual_energy_cost_adj"] = df["annual_electric_cost_adj"] + df["annual_gas_cost_adj"]
    return df


ELECTRIC = [
    Territory(1, "Big IOU", "Investor Owned", 600.0, shares({"00100": 1.0, "00200": 1.0})),
    Territory(2, "Rural Coop", "Cooperative", 900.0, shares({"00300": 1.0})),
    Territory(3, "Tiny Coop", "Cooperative", 5000.0, shares({"00200": 0.1})),
]
GAS = [Territory(10, "Gas Co", "Investor Owned", 360.0, shares({"00100": 1.0, "00200": 1.0}))]


def by_id(table: pd.DataFrame) -> pd.DataFrame:
    return table.set_index("utility_id")


class TestTerritoryOverlap:
    def test_full_coverage_is_one(self) -> None:
        s = pd.Series({"00100": 1.0, "00200": 1.0})
        h = pd.Series({"00100": 20.0, "00200": 30.0})
        assert territory_overlap(s, h) == pytest.approx(1.0)

    def test_sliver_equals_its_share(self) -> None:
        s = pd.Series({"00200": 0.1})
        h = pd.Series({"00200": 30.0})
        assert territory_overlap(s, h) == pytest.approx(0.1)

    def test_no_households_is_zero(self) -> None:
        s = pd.Series({"00900": 0.5})
        assert territory_overlap(s, pd.Series({"00100": 10.0})) == 0.0


class TestEstimateTerritoryFactors:
    def test_own_and_pooled_factors(self) -> None:
        df = microdata()
        payer = df["ELEP"].notna() & (df["GASFP"] != 2)
        table = by_id(
            estimate_territory_factors(
                df, ELECTRIC, cost_col="annual_electric_cost_adj", payer=payer, fuel="electric"
            )
        )
        assert table.loc[1, "observed_avg_annual_bill"] == pytest.approx(1600.0)
        assert table.loc[1, "factor"] == pytest.approx(0.375)
        assert table.loc[1, "factor_source"] == "own"
        assert table.loc[2, "factor"] == pytest.approx(0.5)
        assert not table.loc[3, "reliable"]
        assert table.loc[3, "overlap"] == pytest.approx(0.1)
        assert table.loc[3, "factor"] == pytest.approx(0.5)
        assert table.loc[3, "factor_source"] == "pooled_ownership"

    def test_class_without_reliable_member_pools_all(self) -> None:
        df = microdata()
        tiny_muni = Territory(4, "Tiny Muni", "Municipal", 700.0, shares({"00200": 0.1}))
        table = by_id(
            estimate_territory_factors(
                df,
                [ELECTRIC[0], ELECTRIC[1], tiny_muni],
                cost_col="annual_electric_cost_adj",
                payer=df["ELEP"].notna() & (df["GASFP"] != 2),
            )
        )
        pooled = (600.0 * 30.0 + 900.0 * 10.0) / (1600.0 * 30.0 + 1800.0 * 10.0)
        assert table.loc[4, "factor_source"] == "pooled_all"
        assert table.loc[4, "factor"] == pytest.approx(pooled)

    def test_nothing_reliable_leaves_factor_one(self) -> None:
        df = microdata()
        table = estimate_territory_factors(
            df, [ELECTRIC[2]], cost_col="annual_electric_cost_adj", payer=df["ELEP"].notna()
        )
        assert table.loc[0, "factor"] == 1.0
        assert table.loc[0, "factor_source"] == "uncalibrated"

    def test_duplicate_ids_rejected(self) -> None:
        df = microdata()
        with pytest.raises(ValueError, match="unique"):
            estimate_territory_factors(
                df,
                [ELECTRIC[0], ELECTRIC[0]],
                cost_col="annual_electric_cost_adj",
                payer=df["ELEP"].notna(),
            )


class TestBlendTerritoryValues:
    def test_share_weighted_mean(self) -> None:
        blended = blend_territory_values({1: 0.375, 2: 0.5, 3: 0.5}, ELECTRIC)
        assert blended["00100"] == pytest.approx(0.375)
        assert blended["00200"] == pytest.approx((0.375 + 0.1 * 0.5) / 1.1)
        assert blended["00300"] == pytest.approx(0.5)
        assert "00400" not in blended.index

    def test_non_finite_values_skipped(self) -> None:
        blended = blend_territory_values({1: 0.375, 3: float("nan")}, ELECTRIC)
        assert blended["00200"] == pytest.approx(0.375)


class TestApplyTerritoryCalibration:
    def test_rescales_each_group(self) -> None:
        df = microdata()
        result = apply_territory_calibration(df, electric=ELECTRIC, gas=GAS)
        rows = df.set_index("SERIALNO")
        f200 = (0.375 + 0.1 * 0.5) / 1.1
        assert rows.loc["A", "annual_electric_cost_adj"] == pytest.approx(450.0)
        assert rows.loc["A", "annual_gas_cost_adj"] == pytest.approx(360.0)
        assert rows.loc["A", "annual_energy_cost_adj"] == pytest.approx(810.0)
        assert rows.loc["B", "annual_energy_cost_adj"] == pytest.approx(450.0)
        assert rows.loc["C", "annual_electric_cost_adj"] == pytest.approx(2400.0 * f200)
        assert rows.loc["D", "annual_electric_cost_adj"] == pytest.approx(960.0)
        assert rows.loc["E", "annual_energy_cost_adj"] == 0.0
        assert rows.loc["F", "annual_electric_cost_adj"] == pytest.approx(900.0)
        assert rows.loc["G", "annual_electric_cost_adj"] == pytest.approx(1200.0)

        combined = by_id(result.combined)
        assert combined.loc[1, "target_annual_bill"] == pytest.approx(960.0)
        assert combined.loc[1, "factor"] == pytest.approx(960.0 / 3600.0)
        assert combined.loc[2, "factor_source"] == "pooled_all"
        factors = result.puma_factors.set_index("PUMA")
        assert np.isnan(factors.loc["00400", "electric_factor"])
        assert np.isnan(factors.loc["00300", "gas_factor"])
        assert factors.loc["00100", "gas_factor"] == pytest.approx(0.6)

    def test_without_gas_flag_combined_bills_count_as_electric(self) -> None:
        df = microdata().drop(columns="GASFP")
        result = apply_territory_calibration(df, electric=ELECTRIC, gas=GAS)
        assert result.combined is None
        table = by_id(result.electric)
        assert table.loc[1, "observed_avg_annual_bill"] == pytest.approx(2100.0)
        f200 = (600.0 / 2100.0 + 0.1 * 0.5) / 1.1
        assert df.set_index("SERIALNO").loc["D", "annual_electric_cost_adj"] == pytest.approx(
            3600.0 * f200
        )

    def test_electric_only_leaves_gas_untouched(self) -> None:
        df = microdata()
        result = apply_territory_calibration(df, electric=ELECTRIC)
        assert result.gas is None and result.combined is None
        rows = df.set_index("SERIALNO")
        assert rows.loc["A", "annual_gas_cost_adj"] == pytest.approx(600.0)
        f200 = (600.0 / 2100.0 + 0.1 * 0.5) / 1.1
        assert rows.loc["D", "annual_electric_cost_adj"] == pytest.approx(3600.0 * f200)

    def test_needs_some_territory(self) -> None:
        with pytest.raises(ValueError, match="territories"):
            apply_territory_calibration(microdata())


def test_build_territories_from_polygons(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import box

    from energy_equity.geo.allocation import assign_puma_to_tracts
    from energy_equity.io.geo import read_tiger_zip
    from tests.fixtures.synthetic_geo import make_geo_fixtures, make_tract_households

    geo = make_geo_fixtures(tmp_path)
    tracts = assign_puma_to_tracts(
        read_tiger_zip(geo["tract_zip"]), read_tiger_zip(geo["puma_zip"])
    )
    polygons = gpd.GeoDataFrame(
        {"Name": ["West piece", "East piece"]},
        geometry=[box(1.5, 0, 2.0, 1), box(2.0, 0, 2.5, 1)],
        crs="EPSG:5070",
    )
    eia_csv = tmp_path / "eia861.csv"
    pd.DataFrame(
        [
            {
                "utility_number": 15466,
                "utility_name": "Public Service Co of Colorado",
                "state": "CO",
                "customer_class": "residential",
                "revenue_thousand_dollars": 1200.0,
                "customers": 1000,
                "ownership": "Investor Owned",
                "year": 2024,
            }
        ]
    ).to_csv(eia_csv, index=False)
    crosswalk = pd.DataFrame(
        {"territory_name": ["West piece", "East piece"], "eia_id": [15466, 15466]}
    )

    territories = build_territories(
        polygons,
        crosswalk,
        fuel="electric",
        tracts_with_puma=tracts,
        tract_households=make_tract_households(),
        state="CO",
        year=2024,
        eia_csv=str(eia_csv),
    )

    assert len(territories) == 1
    t = territories[0]
    assert t.utility_id == 15466
    assert t.ownership == "Investor Owned"
    assert t.target_annual_bill == pytest.approx(1200.0)
    got = t.shares.set_index("PUMA")["share_households_in_service"]
    assert got["00800"] == pytest.approx(0.25)
    assert got["00900"] == pytest.approx(0.25)

    with pytest.raises(ValueError, match="no electric territory polygon"):
        build_territories(
            polygons,
            pd.DataFrame({"territory_name": ["Renamed"], "eia_id": [15466]}),
            fuel="electric",
            tracts_with_puma=tracts,
            tract_households=make_tract_households(),
            state="CO",
            year=2024,
            eia_csv=str(eia_csv),
        )


def test_territories_from_config(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import box

    from energy_equity.config import Config
    from energy_equity.territory_calibration import territories_from_config
    from tests.fixtures.synthetic_geo import make_geo_fixtures, make_tract_households

    geo = make_geo_fixtures(tmp_path)
    polygons_path = tmp_path / "electric.gpkg"
    gpd.GeoDataFrame(
        {"Utility": ["Service area"]}, geometry=[box(1.5, 0, 2.5, 1)], crs="EPSG:5070"
    ).to_file(polygons_path, driver="GPKG")
    crosswalk_path = tmp_path / "electric_crosswalk.csv"
    pd.DataFrame({"territory_name": ["Service area"], "eia_id": [15466]}).to_csv(
        crosswalk_path, index=False
    )
    eia_csv = tmp_path / "eia861.csv"
    pd.DataFrame(
        [
            {
                "utility_number": 15466,
                "utility_name": "Public Service Co of Colorado",
                "state": "CO",
                "customer_class": "residential",
                "revenue_thousand_dollars": 1500.0,
                "customers": 1000,
                "ownership": "Investor Owned",
                "year": 2023,
            }
        ]
    ).to_csv(eia_csv, index=False)
    cfg = Config.from_mapping(
        {
            "project": {"name": "t", "output_dir": str(tmp_path / "out")},
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(tmp_path / "x.shp")},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {
                "hud_ami_csv": str(tmp_path / "hud.csv"),
                "tiger_tract_zip": str(geo["tract_zip"]),
                "tiger_puma_zip": str(geo["puma_zip"]),
                "eia861_csv": str(eia_csv),
            },
            "calibration": {
                "territories": {
                    "electric_territories": str(polygons_path),
                    "electric_crosswalk": str(crosswalk_path),
                    "name_column": "Utility",
                    "eia_year": 2023,
                }
            },
        }
    )

    electric, gas = territories_from_config(cfg, tract_households=make_tract_households())

    assert gas == []
    assert len(electric) == 1
    assert electric[0].target_annual_bill == pytest.approx(1500.0)
    shares = electric[0].shares.set_index("PUMA")["share_households_in_service"]
    assert shares["00800"] == pytest.approx(0.25)
    assert shares["00900"] == pytest.approx(0.25)
