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


def _base_payload() -> dict:
    return {
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
    }


def test_calibration_defaults_off() -> None:
    cfg = Config.from_mapping(_base_payload())
    assert cfg.calibration.electric is None
    assert cfg.data_sources.eia861_csv is None


def test_calibration_with_direct_target() -> None:
    payload = _base_payload()
    payload["calibration"] = {"electric": {"target_annual_bill": 1150.0}}
    cfg = Config.from_mapping(payload)
    assert cfg.calibration.electric is not None
    assert cfg.calibration.electric.target_annual_bill == pytest.approx(1150.0)
    assert cfg.calibration.electric.apply is False


def test_calibration_requires_target_or_utility() -> None:
    payload = _base_payload()
    payload["calibration"] = {"electric": {"apply": True}}
    with pytest.raises(Exception, match="utility_number"):
        Config.from_mapping(payload)


def test_calibration_utility_without_csv_ok() -> None:
    payload = _base_payload()
    payload["calibration"] = {"electric": {"utility_number": 15466}}
    cfg = Config.from_mapping(payload)
    assert cfg.calibration.electric.utility_number == 15466
    assert cfg.data_sources.eia861_csv is None


def test_calibration_csv_with_utility_ok() -> None:
    payload = _base_payload()
    payload["data_sources"]["eia861_csv"] = "./eia861.csv"
    payload["calibration"] = {"electric": {"utility_number": 15466, "eia861_year": 2023}}
    cfg = Config.from_mapping(payload)
    assert cfg.calibration.electric.utility_number == 15466


def test_gap_thresholds_accepted() -> None:
    payload = _base_payload()
    payload["pipelines"] = {"eligibility_analysis": {"gap_thresholds": [0.03, 0.06]}}
    cfg = Config.from_mapping(payload)
    assert cfg.pipelines.eligibility_analysis.gap_thresholds == [0.03, 0.06]


def test_gap_thresholds_default_none() -> None:
    cfg = Config.from_mapping(_base_payload())
    assert cfg.pipelines.eligibility_analysis.gap_thresholds is None


def test_gap_thresholds_out_of_range_rejected() -> None:
    payload = _base_payload()
    payload["pipelines"] = {"eligibility_analysis": {"gap_thresholds": [0.06, 1.5]}}
    with pytest.raises(Exception, match="0, 1"):
        Config.from_mapping(payload)


def test_gap_thresholds_empty_rejected() -> None:
    payload = _base_payload()
    payload["pipelines"] = {"eligibility_analysis": {"gap_thresholds": []}}
    with pytest.raises(Exception, match="non-empty"):
        Config.from_mapping(payload)


def test_calibration_unknown_key_rejected() -> None:
    payload = _base_payload()
    payload["calibration"] = {"electric": {"target_annual_bill": 1150.0, "typo_key": 1}}
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


def test_gas_calibration_with_company_ok() -> None:
    payload = _base_payload()
    payload["calibration"] = {"gas": {"company_id": 17611459}}
    cfg = Config.from_mapping(payload)
    assert cfg.calibration.gas.company_id == 17611459
    assert cfg.calibration.gas.apply is False


def test_gas_calibration_requires_target_or_company() -> None:
    payload = _base_payload()
    payload["calibration"] = {"gas": {"apply": True}}
    with pytest.raises(Exception, match="company_id"):
        Config.from_mapping(payload)


def test_both_fuel_blocks_accepted() -> None:
    payload = _base_payload()
    payload["calibration"] = {
        "electric": {"utility_number": 15466},
        "gas": {"company_id": 17611459},
    }
    cfg = Config.from_mapping(payload)
    assert cfg.calibration.electric is not None
    assert cfg.calibration.gas is not None


TERRITORIES = {
    "electric_territories": "./utility_electric.shp",
    "electric_crosswalk": "./electric_crosswalk.csv",
    "gas_territories": "./gas.shp",
    "gas_crosswalk": "./gas_crosswalk.csv",
}


def test_territory_calibration_block_loads() -> None:
    payload = _base_payload()
    payload["calibration"] = {"territories": dict(TERRITORIES)}
    cfg = Config.from_mapping(payload)
    settings = cfg.calibration.territories
    assert settings is not None
    assert settings.electric_crosswalk == Path("./electric_crosswalk.csv")
    assert settings.min_overlap == pytest.approx(0.35)
    assert settings.eia_year is None
    assert settings.name_column == "Name"


def test_territory_calibration_one_fuel_ok() -> None:
    payload = _base_payload()
    payload["calibration"] = {
        "territories": {
            "gas_territories": TERRITORIES["gas_territories"],
            "gas_crosswalk": TERRITORIES["gas_crosswalk"],
        }
    }
    assert Config.from_mapping(payload).calibration.territories.electric_territories is None


@pytest.mark.parametrize(
    ("territories", "message"),
    [
        ({"gas_crosswalk": "./g.csv"}, "gas_crosswalk without gas_territories"),
        (
            {
                "electric_territories": "./e.shp",
                "electric_crosswalk": "./e.csv",
                "gas_crosswalk": "./g.csv",
            },
            "gas_crosswalk without",
        ),
        ({"min_overlap": 0.5}, "needs electric or gas"),
    ],
)
def test_territory_calibration_rejects_incomplete_blocks(territories: dict, message: str) -> None:
    payload = _base_payload()
    payload["calibration"] = {"territories": territories}
    with pytest.raises(ValueError, match=message):
        Config.from_mapping(payload)


def test_territory_calibration_excludes_single_territory_blocks() -> None:
    payload = _base_payload()
    payload["calibration"] = {
        "territories": dict(TERRITORIES),
        "electric": {"target_annual_bill": 1000.0},
    }
    with pytest.raises(ValueError, match="cannot be combined"):
        Config.from_mapping(payload)


def test_territory_calibration_crosswalks_optional() -> None:
    payload = _base_payload()
    payload["calibration"] = {"territories": {"electric_territories": "./e.shp"}}
    settings = Config.from_mapping(payload).calibration.territories
    assert settings.electric_crosswalk is None
    assert settings.gas_territories is None
