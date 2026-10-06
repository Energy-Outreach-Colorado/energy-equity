"""Tests for `bootstrap.fetch_inputs`, `ee data fetch` and null-path runs, all offline."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import energy_equity.bootstrap as bootstrap
import energy_equity.io.census_inputs as ci
from energy_equity.cli import app
from energy_equity.config import Config
from tests.fixtures.fake_downloads import PUMS, TIGER, FakeDownloads, zip_bytes

ALL_FILES = {
    f"{PUMS}/2024/1-Year/csv_hco.zip": zip_bytes(),
    f"{PUMS}/2024/1-Year/csv_pco.zip": zip_bytes(),
    f"{TIGER}/TIGER2024/TRACT/tl_2024_08_tract.zip": zip_bytes(),
    f"{TIGER}/TIGER2024/PUMA20/tl_2024_08_puma20.zip": zip_bytes(),
}


def payload(tmp_path: Path, **sources: str) -> dict:
    return {
        "project": {
            "name": "t",
            "output_dir": str(tmp_path / "out"),
            "cache_dir": str(tmp_path / "cache"),
        },
        "geography": {
            "state_fips": "08",
            "state_abbr": "CO",
            "service_area": {"shapefile": str(tmp_path / "service.shp")},
        },
        "vintages": {"acs_year": 2024, "pums_year": 2024, "hud_ami_fy": 2025, "tiger_year": 2024},
        "data_sources": {"hud_ami_csv": str(tmp_path / "hud.csv"), **sources},
    }


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fake every download `fetch_inputs` makes and record the relationship-file calls."""

    def install(answers: dict[str, bytes | int]) -> FakeDownloads:
        downloads = FakeDownloads(answers)
        monkeypatch.setattr(ci, "download_if_needed", downloads)

        def urban(cache_dir: Path, tiger_year: int, *, force: bool = False) -> Path:
            path = Path(cache_dir) / f"tl_{tiger_year}_us_uac20.zip"
            path.write_bytes(zip_bytes())
            return path

        def relationship(cache_dir: Path, state_fips: str) -> None:
            (Path(cache_dir) / "2020_Census_Tract_to_2020_PUMA.txt").write_text("STATEFP\n08\n")

        monkeypatch.setattr(bootstrap, "ensure_urban_areas_zip", urban)
        monkeypatch.setattr(bootstrap, "load_tract_to_puma_relationship", relationship)
        return downloads

    return install


def by_name(results: list[bootstrap.FetchResult]) -> dict[str, bootstrap.FetchResult]:
    return {r.name: r for r in results}


def test_downloads_then_reports_cached(offline, tmp_path: Path) -> None:
    offline(ALL_FILES)
    cfg = Config.from_mapping(payload(tmp_path))
    first = by_name(bootstrap.fetch_inputs(cfg, include_api=False))
    assert {
        first[n].status for n in ("PUMS housing", "PUMS person", "TIGER tract", "TIGER puma")
    } == {"downloaded"}
    assert first["TIGER urban areas"].status == "downloaded"
    assert first["Tract to PUMA relationship"].status == "downloaded"
    assert first["ACS tract households"].status == "skipped"
    assert first["PUMS housing"].size_bytes > 0

    second = by_name(bootstrap.fetch_inputs(cfg, include_api=False))
    assert {r.status for name, r in second.items() if not name.startswith("ACS")} == {"cached"}


def test_dry_run_downloads_nothing(offline, tmp_path: Path) -> None:
    downloads = offline(ALL_FILES)
    cfg = Config.from_mapping(payload(tmp_path))
    results = by_name(bootstrap.fetch_inputs(cfg, dry_run=True, include_api=False))
    assert downloads.calls == []
    assert results["TIGER puma"].status == "planned"
    assert "PUMA20/tl_2024_08_puma20.zip or " in results["TIGER puma"].detail


def test_configured_and_failed_inputs(offline, tmp_path: Path) -> None:
    offline({f"{TIGER}/TIGER2024/TRACT/tl_2024_08_tract.zip": zip_bytes()})
    local = tmp_path / "csv_hco.zip"
    local.write_bytes(zip_bytes())
    cfg = Config.from_mapping(payload(tmp_path, pums_housing_zip=str(local)))
    results = by_name(bootstrap.fetch_inputs(cfg, include_api=False))
    assert results["PUMS housing"].status == "configured"
    assert results["PUMS housing"].path == local
    assert results["PUMS person"].status == "failed"
    assert "has been released" in results["PUMS person"].detail
    assert results["TIGER tract"].status == "downloaded"


def test_api_inputs_need_a_key(offline, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    offline(ALL_FILES)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    results = by_name(bootstrap.fetch_inputs(Config.from_mapping(payload(tmp_path))))
    assert results["ACS county list"].status == "skipped"
    assert "CENSUS_API_KEY" in results["ACS county list"].detail


def test_api_inputs_report_cache_and_failures(
    offline, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    offline(ALL_FILES)
    monkeypatch.setenv("CENSUS_API_KEY", "key")
    cfg = Config.from_mapping(payload(tmp_path))
    households = tmp_path / "cache" / "acs_tract_households_08.csv"

    def write_households(*args: object, **kwargs: object) -> None:
        households.write_text("tract_geoid,households\n08001000100,10\n")

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Census API call failed")

    monkeypatch.setattr(bootstrap, "resolve_tract_households", write_households)
    monkeypatch.setattr(bootstrap, "fetch_with_year_fallback", fail)
    monkeypatch.setattr(bootstrap.CensusClient, "fetch_b19001_tracts", fail)
    results = by_name(bootstrap.fetch_inputs(cfg))
    assert results["ACS tract households"].status == "downloaded"
    assert results["ACS tract households"].path == households
    assert results["ACS county list"].status == "failed"
    assert results["ACS B19001 tract incomes"].status == "failed"


def write_config(tmp_path: Path, **sources: str) -> Path:
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload(tmp_path, **sources)))
    return path


def test_cli_fetch_prints_one_line_per_input(offline, tmp_path: Path) -> None:
    offline(ALL_FILES)
    result = CliRunner().invoke(
        app, ["data", "fetch", "--config", str(write_config(tmp_path)), "--skip-api"]
    )
    assert result.exit_code == 0, result.output
    assert "downloaded  PUMS housing" in result.output
    assert "skipped     ACS county list" in result.output
    assert f"Cache directory: {tmp_path / 'cache'}" in result.output


def test_cli_fetch_fails_when_an_input_fails(offline, tmp_path: Path) -> None:
    offline({})
    result = CliRunner().invoke(
        app, ["data", "fetch", "--config", str(write_config(tmp_path)), "--skip-api"]
    )
    assert result.exit_code == 1
    assert "failed      PUMS housing" in result.output


def test_starter_config_validates_with_null_inputs(tmp_path: Path) -> None:
    output = tmp_path / "config.yaml"
    result = CliRunner().invoke(
        app, ["config", "init", "--state", "co", "--state-fips", "08", "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    cfg = Config.from_yaml(output)
    assert cfg.data_sources.pums_housing_zip is None
    assert cfg.data_sources.tiger_puma_zip is None
    assert "ee data fetch" in result.output


def test_prepare_downloads_null_pums_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from energy_equity.pums.prepare import prepare_household_microdata
    from tests.fixtures.synthetic_pums import (
        synthesize_ami80_by_puma,
        synthesize_pums_housing,
        synthesize_pums_person,
        write_pums_zips,
    )

    housing = synthesize_pums_housing(n_households=50, seed=3)
    source = tmp_path / "source"
    source.mkdir()
    housing_zip, person_zip = write_pums_zips(
        source, housing=housing, person=synthesize_pums_person(housing, seed=4)
    )
    downloads = FakeDownloads(
        {
            f"{PUMS}/2024/1-Year/csv_hco.zip": housing_zip.read_bytes(),
            f"{PUMS}/2024/1-Year/csv_pco.zip": person_zip.read_bytes(),
        }
    )
    monkeypatch.setattr(ci, "download_if_needed", downloads)
    data = payload(tmp_path)
    data["weights"] = {"compute_moe": False}
    md = prepare_household_microdata(
        Config.from_mapping(data), ami80_by_puma=synthesize_ami80_by_puma()
    )
    assert len(md.df) == len(housing)
    assert zipfile.is_zipfile(tmp_path / "cache" / "pums" / "2024_1yr" / "csv_hco.zip")
    assert len(downloads.calls) == 2
