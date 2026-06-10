"""End-to-end tests for reporting.run (regression for the tract_geoid merge crash)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import responses
from responses.matchers import query_param_matcher

from energy_equity.census.api import B19001_VARIABLES, BASE_URL
from energy_equity.config import Config
from energy_equity.pipelines import reporting
from tests.fixtures.synthetic_geo import make_geo_fixtures


def _cfg(tmp_path: Path, geo: dict[str, Path]) -> Config:
    cache = tmp_path / "cache"
    cache.mkdir()
    return Config.from_mapping(
        {
            "project": {
                "name": "reporting",
                "output_dir": str(tmp_path / "out"),
                "cache_dir": str(cache),
            },
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(geo["service_path"])},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {
                "hud_ami_csv": str(tmp_path / "noop.csv"),
                "tiger_tract_zip": str(geo["tract_zip"]),
            },
        }
    )


@responses.activate
def test_reporting_run_full_path_mocked_census(tmp_path: Path) -> None:
    geo = make_geo_fixtures(tmp_path)
    cfg = _cfg(tmp_path, geo)

    # B19001 rows for tracts that match the synthetic tract GEOID20s (county 101).
    header = [*B19001_VARIABLES, "state", "county", "tract"]
    bin_counts = [str(100 + 10 * i) for i in range(len(B19001_VARIABLES))]
    payload = [
        header,
        [*bin_counts, "08", "101", "000100"],
        [*bin_counts, "08", "101", "000200"],
        [*bin_counts, "08", "101", "000300"],
    ]
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=payload,
        match=[query_param_matcher({"get": ",".join(B19001_VARIABLES)}, strict_match=False)],
    )

    written = reporting.run(cfg)  # previously raised KeyError: 'tract_geoid'

    out = Path(cfg.project.output_dir)
    assert (out / "income_distribution_service_vs_state.csv").exists()
    assert (out / "income_cutpoint_shares.csv").exists()
    assert (out / "regressivity_table.csv").exists()

    comparison = written["income_distribution_service_vs_state"]
    assert len(comparison) == 16  # 16 B19001 bins
    # The service polygon overlaps tracts in county 101, so some households land in-service.
    assert comparison["service_households"].sum() > 0


def test_reporting_run_renames_geoid_key(tmp_path: Path) -> None:
    """Passing B19001 with a `geoid` column (as the real parser emits) must not crash."""
    b19001 = pd.DataFrame(
        {col: [10, 20] for col in B19001_VARIABLES} | {"geoid": ["08101000100", "08101000200"]}
    )
    shares = pd.DataFrame(
        {
            "tract_geoid": ["08101000100", "08101000200"],
            "share_tract_in_service": [1.0, 0.5],
        }
    )
    cfg = Config.from_mapping(
        {
            "project": {
                "name": "r",
                "output_dir": str(tmp_path / "out"),
                "cache_dir": str(tmp_path / "c"),
            },
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(tmp_path / "noop.shp")},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {"hud_ami_csv": str(tmp_path / "noop.csv")},
        }
    )

    written = reporting.run(cfg, b19001_per_tract=b19001, tract_service_shares=shares)
    assert len(written["income_distribution_service_vs_state"]) == 16
    assert written["income_distribution_service_vs_state"]["service_households"].sum() > 0


def _mock_b19001() -> None:
    header = [*B19001_VARIABLES, "state", "county", "tract"]
    bin_counts = [str(100 + 10 * i) for i in range(len(B19001_VARIABLES))]
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=[header, [*bin_counts, "08", "101", "000100"], [*bin_counts, "08", "101", "000200"]],
        match=[query_param_matcher({"get": ",".join(B19001_VARIABLES)}, strict_match=False)],
    )


@responses.activate
def test_reporting_run_writes_figures(tmp_path: Path) -> None:
    pytest.importorskip("plotly")
    pytest.importorskip("kaleido")
    geo = make_geo_fixtures(tmp_path)
    cfg = _cfg(tmp_path, geo)
    # Add the PUMA layer so choropleths can render.
    cfg.data_sources.tiger_puma_zip = geo["puma_zip"]

    # Pre-write the sibling CSVs the figure step reads (as `run all` would have).
    out = Path(cfg.project.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "segment": ["all", "all"],
            "population": ["All households", "All households"],
            "monthly_fixed_charge_increase": [0.0, 5.0],
            "newly_energy_burdened_households": [0.0, 120.0],
            "newly_highly_energy_burdened_households": [0.0, 30.0],
        }
    ).to_csv(out / "scenario_sweep.csv", index=False)
    pd.DataFrame(
        {
            "segment": ["all"],
            "population": ["All households"],
            "baseline_energy_burdened_households": [1000.0],
            "newly_energy_burdened_households": [150.0],
        }
    ).to_csv(out / "fixed_charge_headline_summary.csv", index=False)
    pd.DataFrame(
        {
            "head_race": ["White alone", "Two or more races"],
            "current_eligible_households": [500.0, 100.0],
            "proposed_eligible_households": [900.0, 220.0],
        }
    ).to_csv(out / "demographics_race.csv", index=False)
    pd.DataFrame(
        {
            "segment": ["all", "all"],
            "burden_band": ["<2.5% burden", ">=6% burden"],
            "share_of_low_income": [0.4, 0.2],
        }
    ).to_csv(out / "low_income_burden_bands.csv", index=False)
    pd.DataFrame(
        {
            "PUMA": ["00800", "00900"],
            "current_eligible_households": [1200.0, 800.0],
            "newly_added_households": [300.0, 150.0],
        }
    ).to_csv(out / "puma_summary.csv", index=False)

    _mock_b19001()
    reporting.run(cfg)

    fig_dir = out / "figures"
    expected = [
        "income_comparison.png",
        "regressivity_curve.png",
        "scenario_sweep.png",
        "energy_burden_waterfall.png",
        "demographics_race.png",
        "burden_bands.png",
        "choropleth_current_eligible.png",
        "choropleth_newly_added.png",
    ]
    for fname in expected:
        p = fig_dir / fname
        assert p.exists(), f"missing figure {fname}"
        assert p.stat().st_size > 0


@responses.activate
def test_reporting_run_skips_figures_with_missing_inputs(tmp_path: Path) -> None:
    pytest.importorskip("plotly")
    pytest.importorskip("kaleido")
    geo = make_geo_fixtures(tmp_path)
    cfg = _cfg(tmp_path, geo)  # no tiger_puma_zip, no sibling CSVs pre-written

    _mock_b19001()
    reporting.run(cfg)  # must not raise

    fig_dir = Path(cfg.project.output_dir) / "figures"
    # Income figures render (data is in-memory); scenario/choropleth skip (inputs absent).
    assert (fig_dir / "income_comparison.png").exists()
    assert (fig_dir / "regressivity_curve.png").exists()
    assert not (fig_dir / "scenario_sweep.png").exists()
    assert not (fig_dir / "choropleth_current_eligible.png").exists()
