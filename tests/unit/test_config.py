"""Validation tests for energy_equity.config."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from energy_equity.config import Config

VALID_YAML = dedent(
    """
    project:
      name: pueblo_county_2024
      output_dir: ./out
    geography:
      state_fips: "08"
      state_abbr: CO
      service_area:
        shapefile: ./data/pueblo.shp
    vintages:
      acs_year: 2024
      pums_year: 2024
      hud_ami_fy: 2025
      tiger_year: 2024
    data_sources:
      hud_ami_csv: ./data/hud_ami.csv
    """
).strip()


def test_minimal_yaml_loads(tmp_path: Path) -> None:
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(VALID_YAML)
    cfg = Config.from_yaml(yaml_path)
    assert cfg.project.name == "pueblo_county_2024"
    assert cfg.geography.state_fips == "08"
    assert cfg.geography.state_abbr == "CO"
    assert cfg.thresholds.energy_burden_threshold == pytest.approx(0.06)
    assert cfg.pipelines.eligibility_analysis.program_name == "PIPP"
    assert cfg.weights.replicate_count == 80


def test_unknown_key_rejected(tmp_path: Path) -> None:
    bad = VALID_YAML + "\nunknown_section:\n  foo: 1\n"
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text(bad)
    with pytest.raises(Exception):
        Config.from_yaml(yaml_path)


def test_state_fips_validation() -> None:
    with pytest.raises(Exception):
        Config.from_mapping(
            {
                "project": {"name": "x", "output_dir": "./out"},
                "geography": {
                    "state_fips": "8",  # wrong: not 2-digit
                    "state_abbr": "CO",
                    "service_area": {"shapefile": "./x.shp"},
                },
                "vintages": {
                    "acs_year": 2024,
                    "pums_year": 2024,
                    "hud_ami_fy": 2025,
                    "tiger_year": 2024,
                },
                "data_sources": {"hud_ami_csv": "./hud.csv"},
            }
        )


def test_burden_threshold_ordering_enforced() -> None:
    """high_energy_burden_threshold must exceed energy_burden_threshold."""
    payload = {
        "project": {"name": "x", "output_dir": "./out"},
        "geography": {
            "state_fips": "08",
            "state_abbr": "CO",
            "service_area": {"shapefile": "./x.shp"},
        },
        "vintages": {
            "acs_year": 2024,
            "pums_year": 2024,
            "hud_ami_fy": 2025,
            "tiger_year": 2024,
        },
        "data_sources": {"hud_ami_csv": "./hud.csv"},
        "thresholds": {
            "energy_burden_threshold": 0.10,
            "high_energy_burden_threshold": 0.06,
        },
    }
    with pytest.raises(Exception):
        Config.from_mapping(payload)


def test_defaults_for_optional_sections() -> None:
    """census_api, thresholds, weights, pipelines all have working defaults."""
    cfg = Config.from_mapping(
        {
            "project": {"name": "x", "output_dir": "./out"},
            "geography": {
                "state_fips": "17",
                "state_abbr": "il",  # lowercased; validator should uppercase
                "service_area": {"shapefile": "./x.shp"},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {"hud_ami_csv": "./hud.csv"},
        }
    )
    assert cfg.geography.state_abbr == "IL"
    assert cfg.census_api.key_env == "CENSUS_API_KEY"
    assert cfg.weights.compute_moe is True
    assert cfg.pipelines.fixed_charge.monthly_increase == pytest.approx(4.00)
