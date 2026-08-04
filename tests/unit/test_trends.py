"""Unit tests for the trends pipeline table builders."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from energy_equity.config import Config
from energy_equity.pipelines import trends


def write_run_dir(
    tmp_path: Path,
    year: int,
    *,
    total_gap: float,
    gap_moe: float | None,
    burden_rate: float,
    label: str = "svc",
) -> Path:
    run_dir = tmp_path / f"run_{year}"
    run_dir.mkdir()
    pd.DataFrame(
        [
            {
                "segment": "all",
                "population": "All households",
                "threshold": 0.06,
                "gap_valid_households": 1000.0,
                "households_in_gap": 100.0,
                "households_in_gap_moe90": 10.0,
                "households_in_gap_rate": 0.1,
                "total_gap_dollars": total_gap,
                "total_gap_dollars_moe90": gap_moe,
                "mean_gap_per_household_in_gap": total_gap / 100.0,
            }
        ]
    ).to_csv(run_dir / "affordability_gap.csv", index=False)
    pd.DataFrame(
        [{"segment": "all", "hh_eb_w_in_service": 100.0, "hh_eb_w_in_service_moe90": 9.0}]
    ).to_csv(run_dir / f"{label}_totals.csv", index=False)
    pd.DataFrame(
        [
            {
                "segment": "all",
                "rate": "pct_energy_burdened_of_valid",
                "estimate": burden_rate,
                "moe90": 0.01,
            },
            {"segment": "all", "rate": "pct_le80_of_total", "estimate": 0.4, "moe90": 0.02},
        ]
    ).to_csv(run_dir / f"{label}_rates.csv", index=False)
    return run_dir


def make_cfg(tmp_path: Path, runs: list[dict]) -> Config:
    return Config.from_mapping(
        {
            "project": {"name": "trend_run", "output_dir": str(tmp_path / "out")},
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
            "pipelines": {"trends": {"runs": runs, "figures": []}},
        }
    )


def test_trend_tables_and_deltas(tmp_path: Path) -> None:
    d22 = write_run_dir(tmp_path, 2022, total_gap=200_000.0, gap_moe=30_000.0, burden_rate=0.10)
    d23 = write_run_dir(tmp_path, 2023, total_gap=260_000.0, gap_moe=40_000.0, burden_rate=0.12)
    cfg = make_cfg(
        tmp_path,
        [
            {"year": 2023, "output_dir": str(d23)},
            {"year": 2022, "output_dir": str(d22)},
        ],
    )
    written = trends.run(cfg)

    gap = written["trends_affordability_gap"]
    assert gap["year"].tolist() == [2022, 2023]
    assert (gap["dollar_basis"] == "nominal_survey_year").all()

    burden = written["trends_energy_burden"]
    assert burden["year"].tolist() == [2022, 2023]
    assert burden["energy_burden_rate"].tolist() == [0.10, 0.12]
    assert (burden["service"] == "svc").all()

    deltas = written["trends_deltas"]
    gap_delta = deltas[
        (deltas["table"] == "affordability_gap") & (deltas["metric"] == "total_gap_dollars")
    ].iloc[0]
    assert gap_delta["year_from"] == 2022
    assert gap_delta["year_to"] == 2023
    assert gap_delta["delta"] == pytest.approx(60_000.0)
    assert gap_delta["delta_moe90"] == pytest.approx(math.sqrt(30_000.0**2 + 40_000.0**2))

    out_dir = Path(cfg.project.output_dir)
    for fname in (
        "trends_affordability_gap.csv",
        "trends_energy_burden.csv",
        "trends_deltas.csv",
    ):
        assert (out_dir / fname).exists()


def test_delta_moe_nan_when_either_year_missing(tmp_path: Path) -> None:
    d22 = write_run_dir(tmp_path, 2022, total_gap=200_000.0, gap_moe=None, burden_rate=0.10)
    d23 = write_run_dir(tmp_path, 2023, total_gap=260_000.0, gap_moe=40_000.0, burden_rate=0.12)
    cfg = make_cfg(
        tmp_path,
        [
            {"year": 2022, "output_dir": str(d22)},
            {"year": 2023, "output_dir": str(d23)},
        ],
    )
    written = trends.run(cfg)
    deltas = written["trends_deltas"]
    gap_delta = deltas[
        (deltas["table"] == "affordability_gap") & (deltas["metric"] == "total_gap_dollars")
    ].iloc[0]
    assert gap_delta["delta"] == pytest.approx(60_000.0)
    assert pd.isna(gap_delta["delta_moe90"])


def test_empty_runs_rejected(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, [])
    with pytest.raises(ValueError, match="runs is empty"):
        trends.run(cfg)


def test_duplicate_years_rejected(tmp_path: Path) -> None:
    d22 = write_run_dir(tmp_path, 2022, total_gap=1.0, gap_moe=1.0, burden_rate=0.1)
    cfg = make_cfg(
        tmp_path,
        [
            {"year": 2022, "output_dir": str(d22)},
            {"year": 2022, "output_dir": str(d22)},
        ],
    )
    with pytest.raises(ValueError, match="duplicate years"):
        trends.run(cfg)


def test_ambiguous_service_label_rejected(tmp_path: Path) -> None:
    d22 = write_run_dir(tmp_path, 2022, total_gap=1.0, gap_moe=1.0, burden_rate=0.1)
    pd.DataFrame([{"segment": "all", "rate": "x", "estimate": 1.0, "moe90": 0.1}]).to_csv(
        d22 / "other_rates.csv", index=False
    )
    cfg = make_cfg(tmp_path, [{"year": 2022, "output_dir": str(d22)}])
    with pytest.raises(ValueError, match="multiple allocation rate files"):
        trends.run(cfg)


def test_service_label_override(tmp_path: Path) -> None:
    d22 = write_run_dir(tmp_path, 2022, total_gap=1.0, gap_moe=1.0, burden_rate=0.1)
    pd.DataFrame([{"segment": "all", "rate": "x", "estimate": 1.0, "moe90": 0.1}]).to_csv(
        d22 / "other_rates.csv", index=False
    )
    pd.DataFrame([{"segment": "all", "hh_eb_w_in_service": 5.0}]).to_csv(
        d22 / "other_totals.csv", index=False
    )
    cfg = make_cfg(tmp_path, [{"year": 2022, "output_dir": str(d22), "service_label": "svc"}])
    written = trends.run(cfg)
    assert (written["trends_energy_burden"]["service"] == "svc").all()


def test_missing_inputs_tolerated(tmp_path: Path) -> None:
    d22 = write_run_dir(tmp_path, 2022, total_gap=1.0, gap_moe=1.0, burden_rate=0.1)
    bare = tmp_path / "run_2023"
    bare.mkdir()
    cfg = make_cfg(
        tmp_path,
        [
            {"year": 2022, "output_dir": str(d22)},
            {"year": 2023, "output_dir": str(bare)},
        ],
    )
    written = trends.run(cfg)
    assert written["trends_affordability_gap"]["year"].tolist() == [2022]
    assert written["trends_energy_burden"]["year"].tolist() == [2022]
