"""Tests for energy_equity.io.census_inputs and the download helper, all offline.

Every download goes through `download_if_needed`, which these tests replace with a fake
that serves canned answers per URL, so URL construction, fallback order, cache reuse and
error messages are checked without touching census.gov.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import requests

import energy_equity.io.census_inputs as ci
from energy_equity.config import Config
from energy_equity.io.download import DownloadError, download_if_needed
from tests.fixtures.fake_downloads import PUMS, TIGER, FakeDownloads, zip_bytes


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    def install(answers: dict[str, bytes | int]) -> FakeDownloads:
        downloads = FakeDownloads(answers)
        monkeypatch.setattr(ci, "download_if_needed", downloads)
        return downloads

    return install


class TestUrls:
    def test_pums_urls_use_span_folder_and_lower_case_state(self) -> None:
        assert ci.pums_url(2024, 1, "housing", "CO") == f"{PUMS}/2024/1-Year/csv_hco.zip"
        assert ci.pums_url(2023, 5, "person", "il") == f"{PUMS}/2023/5-Year/csv_pil.zip"

    @pytest.mark.parametrize(
        ("year", "filename"),
        [
            (2021, "tl_2021_08_puma10.zip"),
            (2022, "tl_2022_08_puma20.zip"),
            (2023, "tl_2023_08_puma20.zip"),
            (2024, "tl_2024_08_puma20.zip"),
        ],
    )
    def test_puma_layer_tries_both_folders(self, year: int, filename: str) -> None:
        assert ci.tiger_urls("puma", year, "08") == [
            f"{TIGER}/TIGER{year}/PUMA20/{filename}",
            f"{TIGER}/TIGER{year}/PUMA/{filename}",
        ]

    def test_tract_layer_has_one_folder(self) -> None:
        assert ci.tiger_urls("tract", 2024, "17") == [
            f"{TIGER}/TIGER2024/TRACT/tl_2024_17_tract.zip"
        ]


class TestPumaDefinitions:
    @pytest.mark.parametrize(
        ("year", "span", "expected"),
        [(2021, 1, 2010), (2022, 1, 2020), (2024, 1, 2020), (2021, 5, 2010), (2023, 5, 2020)],
    )
    def test_microdata_definitions(self, year: int, span: int, expected: int) -> None:
        assert ci.pums_puma_definition(year, span) == expected

    def test_split_five_year_file_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="PUMA10 and PUMA20"):
            ci.pums_puma_definition(2022, 5)

    def test_mismatch_names_both_years(self) -> None:
        with pytest.raises(ValueError, match="PUMS 2024 1-year uses 2020.*TIGER 2021 has 2010"):
            ci.check_puma_vintages(2024, 1, 2021)

    def test_matching_vintages_pass(self) -> None:
        ci.check_puma_vintages(2024, 1, 2024)
        ci.check_puma_vintages(2021, 5, 2021)


class TestDownloadFirstAvailable:
    def test_falls_back_to_the_next_candidate(self, fake, tmp_path: Path) -> None:
        downloads = fake({"https://b/x.zip": zip_bytes()})
        dest = ci.download_first_available(
            ["https://a/x.zip", "https://b/x.zip"], tmp_path / "x.zip"
        )
        assert downloads.calls == ["https://a/x.zip", "https://b/x.zip"]
        assert zipfile.is_zipfile(dest)

    def test_all_missing_lists_urls_and_hint(self, fake, tmp_path: Path) -> None:
        fake({})
        with pytest.raises(
            ci.CensusInputError, match="Tried: https://a/x.zip, https://b/x.zip. Hint"
        ):
            ci.download_first_available(
                ["https://a/x.zip", "https://b/x.zip"], tmp_path / "x.zip", not_found_hint="Hint"
            )

    def test_server_errors_stop_the_search(self, fake, tmp_path: Path) -> None:
        downloads = fake({"https://a/x.zip": 503, "https://b/x.zip": zip_bytes()})
        with pytest.raises(DownloadError):
            ci.download_first_available(["https://a/x.zip", "https://b/x.zip"], tmp_path / "x.zip")
        assert downloads.calls == ["https://a/x.zip"]

    def test_non_zip_download_is_deleted(self, fake, tmp_path: Path) -> None:
        fake({"https://a/x.zip": b"<html>error</html>"})
        with pytest.raises(ci.CensusInputError, match="not a valid ZIP"):
            ci.download_first_available(["https://a/x.zip"], tmp_path / "x.zip")
        assert not (tmp_path / "x.zip").exists()

    def test_valid_cache_is_reused(self, fake, tmp_path: Path) -> None:
        dest = tmp_path / "x.zip"
        dest.write_bytes(zip_bytes())
        downloads = fake({})
        assert ci.download_first_available(["https://a/x.zip"], dest) == dest
        assert downloads.calls == []

    def test_broken_cache_is_replaced(self, fake, tmp_path: Path) -> None:
        dest = tmp_path / "x.zip"
        dest.write_text("not a zip")
        downloads = fake({"https://a/x.zip": zip_bytes()})
        ci.download_first_available(["https://a/x.zip"], dest)
        assert downloads.calls == ["https://a/x.zip"]
        assert zipfile.is_zipfile(dest)


class TestEnsure:
    def test_pums_lands_in_the_cache_layout(self, fake, tmp_path: Path) -> None:
        fake({f"{PUMS}/2024/1-Year/csv_hco.zip": zip_bytes()})
        path = ci.ensure_pums_zip(tmp_path, year=2024, span=1, kind="housing", state_abbr="CO")
        assert path == tmp_path / "pums" / "2024_1yr" / "csv_hco.zip"

    def test_missing_2020_one_year_explains_why(self, fake, tmp_path: Path) -> None:
        fake({})
        with pytest.raises(ci.CensusInputError, match="experimental data"):
            ci.ensure_pums_zip(tmp_path, year=2020, span=1, kind="housing", state_abbr="CO")

    def test_unreleased_year_says_so(self, fake, tmp_path: Path) -> None:
        fake({})
        with pytest.raises(ci.CensusInputError, match="2025 1-year PUMS has been released"):
            ci.ensure_pums_zip(tmp_path, year=2025, span=1, kind="person", state_abbr="CO")

    def test_tiger_puma_uses_the_older_folder_when_needed(self, fake, tmp_path: Path) -> None:
        downloads = fake({f"{TIGER}/TIGER2023/PUMA/tl_2023_08_puma20.zip": zip_bytes()})
        path = ci.ensure_tiger_zip(tmp_path, layer="puma", tiger_year=2023, state_fips="08")
        assert path == tmp_path / "tiger" / "2023" / "tl_2023_08_puma20.zip"
        assert len(downloads.calls) == 2


def config(tmp_path: Path, **sources: str) -> Config:
    return Config.from_mapping(
        {
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
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {"hud_ami_csv": str(tmp_path / "hud.csv"), **sources},
        }
    )


class TestResolve:
    def test_configured_path_wins(self, fake, tmp_path: Path) -> None:
        local = tmp_path / "mine.zip"
        local.write_bytes(zip_bytes())
        downloads = fake({})
        assert (
            ci.resolve_pums_zip(config(tmp_path, pums_housing_zip=str(local)), "housing") == local
        )
        assert downloads.calls == []

    def test_configured_missing_path_is_an_error(self, fake, tmp_path: Path) -> None:
        downloads = fake({})
        cfg = config(tmp_path, tiger_tract_zip=str(tmp_path / "typo.zip"))
        with pytest.raises(FileNotFoundError, match="set it to null to download"):
            ci.resolve_tiger_zip(cfg, "tract")
        assert downloads.calls == []

    def test_null_path_downloads_into_the_project_cache(self, fake, tmp_path: Path) -> None:
        fake({f"{TIGER}/TIGER2024/TRACT/tl_2024_08_tract.zip": zip_bytes()})
        path = ci.resolve_tiger_zip(config(tmp_path), "tract")
        assert path == tmp_path / "cache" / "tiger" / "2024" / "tl_2024_08_tract.zip"


class TestConfigVintageCheck:
    def test_mismatched_tiger_year_is_rejected(self, tmp_path: Path) -> None:
        payload = config(tmp_path).model_dump(mode="json")
        payload["vintages"]["tiger_year"] = 2021
        with pytest.raises(ValueError, match="wrong polygons"):
            Config.from_mapping(payload)

    def test_split_five_year_file_is_rejected(self, tmp_path: Path) -> None:
        payload = config(tmp_path).model_dump(mode="json")
        payload["vintages"].update(pums_year=2022, pums_span=5, tiger_year=2022)
        with pytest.raises(ValueError, match="2018-2022 5-year"):
            Config.from_mapping(payload)


class FakeResponse:
    def __init__(self, status: int, chunks: list[bytes] | None = None, fail: bool = False) -> None:
        self.status_code = status
        self.chunks = chunks or []
        self.fail = fail

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size: int):
        yield from self.chunks
        if self.fail:
            raise requests.ConnectionError("connection dropped")


class TestDownloadIfNeeded:
    def test_not_found_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls = []
        monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(404))
        with pytest.raises(DownloadError) as info:
            download_if_needed("https://x/f.zip", tmp_path / "f.zip", backoff_sec=0)
        assert info.value.not_found
        assert len(calls) == 1

    def test_server_errors_are_retried(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls = []
        monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(503))
        with pytest.raises(DownloadError) as info:
            download_if_needed("https://x/f.zip", tmp_path / "f.zip", backoff_sec=0)
        assert info.value.status_code == 503
        assert len(calls) == 3

    def test_dropped_stream_leaves_no_partial_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(200, [b"partial"], fail=True)
        )
        with pytest.raises(DownloadError):
            download_if_needed("https://x/f.zip", tmp_path / "f.zip", backoff_sec=0, retries=2)
        assert list(tmp_path.iterdir()) == []


@pytest.mark.network
@pytest.mark.parametrize(
    "url",
    [
        ci.pums_url(2024, 1, "housing", "CO"),
        ci.tiger_urls("puma", 2024, "08")[0],
        ci.tiger_urls("tract", 2024, "08")[0],
    ],
)
def test_census_still_serves_these_urls(url: str) -> None:
    response = requests.head(url, allow_redirects=True, timeout=30)
    assert response.status_code == 200
