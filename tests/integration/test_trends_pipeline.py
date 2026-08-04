"""Integration test for the trends pipeline over two synthetic fixture runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from energy_equity.config import Config
from energy_equity.pipelines import eligibility_analysis, puma_table, service_allocation, trends
from tests.fixtures.synthetic_geo import make_geo_fixtures, make_tract_households
from tests.fixtures.synthetic_pums import (
    synthesize_ami80_by_puma,
    synthesize_pums_housing,
    synthesize_pums_person,
    write_pums_zips,
)


def run_one_year(tmp_path: Path, *, year: int, seed: int) -> Path:
    workdir = tmp_path / f"y{year}"
    workdir.mkdir()
    housing = synthesize_pums_housing(n_households=150, seed=seed)
    person = synthesize_pums_person(housing, seed=seed + 1)
    housing_zip, person_zip = write_pums_zips(workdir, housing=housing, person=person)
    geo = make_geo_fixtures(workdir)

    cfg = Config.from_mapping(
        {
            "project": {"name": f"run{year}", "output_dir": str(workdir / "out")},
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(geo["service_path"])},
            },
            "vintages": {
                "acs_year": year,
                "pums_year": year,
                "hud_ami_fy": 2025,
                "tiger_year": year,
            },
            "data_sources": {
                "hud_ami_csv": str(workdir / "noop.csv"),
                "pums_housing_zip": str(housing_zip),
                "pums_person_zip": str(person_zip),
                "tiger_tract_zip": str(geo["tract_zip"]),
                "tiger_puma_zip": str(geo["puma_zip"]),
            },
            "weights": {"compute_moe": True, "replicate_count": 80},
            "pipelines": {
                "service_allocation": {"build_urban_rural_split": False},
            },
        }
    )
    from energy_equity.pums.prepare import prepare_household_microdata

    md = prepare_household_microdata(cfg, ami80_by_puma=synthesize_ami80_by_puma())
    pt = puma_table.run(cfg, microdata=md, write_demographics=False)
    sa = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=make_tract_households(),
        service_label="test_service",
    )
    eligibility_analysis.run(cfg, microdata=md, service_shares=sa["puma_shares"])
    return workdir / "out"


def test_trends_pipeline_end_to_end(tmp_path: Path) -> None:
    out_2023 = run_one_year(tmp_path, year=2023, seed=7)
    out_2024 = run_one_year(tmp_path, year=2024, seed=21)

    cfg = Config.from_mapping(
        {
            "project": {"name": "trends", "output_dir": str(tmp_path / "trends_out")},
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(tmp_path / "unused.shp")},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {"hud_ami_csv": str(tmp_path / "unused.csv")},
            "pipelines": {
                "trends": {
                    "runs": [
                        {"year": 2023, "output_dir": str(out_2023)},
                        {"year": 2024, "output_dir": str(out_2024)},
                    ]
                }
            },
        }
    )
    written = trends.run(cfg)

    gap = written["trends_affordability_gap"]
    assert set(gap["year"]) == {2023, 2024}
    assert gap["households_in_gap_moe90"].notna().all()

    burden = written["trends_energy_burden"]
    assert set(burden["year"]) == {2023, 2024}
    assert (burden["service"] == "test_service").all()

    deltas = written["trends_deltas"]
    assert (deltas["year_from"] == 2023).all()
    assert (deltas["year_to"] == 2024).all()
    assert deltas[deltas["table"] == "affordability_gap"]["delta_moe90"].notna().all()

    out_dir = Path(cfg.project.output_dir)
    for fname in (
        "trends_affordability_gap.csv",
        "trends_energy_burden.csv",
        "trends_deltas.csv",
    ):
        assert (out_dir / fname).exists()


def test_trends_pipeline_writes_figures(tmp_path: Path) -> None:
    pytest.importorskip("plotly")
    pytest.importorskip("kaleido")
    out_2023 = run_one_year(tmp_path, year=2023, seed=7)
    out_2024 = run_one_year(tmp_path, year=2024, seed=21)

    cfg = Config.from_mapping(
        {
            "project": {"name": "trends", "output_dir": str(tmp_path / "trends_out")},
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(tmp_path / "unused.shp")},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {"hud_ami_csv": str(tmp_path / "unused.csv")},
            "pipelines": {
                "trends": {
                    "runs": [
                        {"year": 2023, "output_dir": str(out_2023)},
                        {"year": 2024, "output_dir": str(out_2024)},
                    ]
                }
            },
        }
    )
    trends.run(cfg)

    fig_dir = Path(cfg.project.output_dir) / "figures"
    for fname in (
        "trend_affordability_gap.png",
        "trend_households_in_gap.png",
        "trend_energy_burden_rate.png",
    ):
        path = fig_dir / fname
        assert path.exists(), f"missing figure: {fname}"
        assert path.stat().st_size > 0
