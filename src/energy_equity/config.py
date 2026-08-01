"""Typed configuration models for energy-equity pipelines.

A single YAML file drives any subset of pipelines. The schema is validated with pydantic v2;
unknown keys are rejected so typos fail fast. Use `Config.from_yaml(path)` or
`Config.from_mapping(dict)` to build a validated config.

The top-level structure mirrors the YAML:

    project: ProjectConfig
    geography: GeographyConfig
    vintages: VintagesConfig
    data_sources: DataSourcesConfig
    census_api: CensusApiConfig
    thresholds: ThresholdsConfig
    weights: WeightsConfig
    calibration: CalibrationConfig
    pipelines: PipelinesConfig
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    """Reject unknown keys to surface typos in YAML configs immediately."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=False)


class ProjectConfig(_StrictModel):
    name: str = Field(description="Short identifier for this run; used in output filenames.")
    output_dir: Path = Field(description="Where pipelines write their CSVs. Created if missing.")
    cache_dir: Path | None = Field(
        default=None,
        description="On-disk cache for downloaded Census responses and intermediates. "
        "Null -> platformdirs default; can also be overridden by the EE_CACHE_DIR env var.",
    )


class ServiceAreaConfig(_StrictModel):
    shapefile: Path = Field(description="Path to a polygon file (.shp, .geojson, .gpkg, .zip).")
    name_field: str | None = Field(
        default=None,
        description="Optional attribute column to label the dissolved service area in outputs.",
    )


class GeographyConfig(_StrictModel):
    state_fips: str = Field(description='Two-digit state FIPS code, e.g. "08" for Colorado.')
    state_abbr: str = Field(description='Two-letter state postal code, e.g. "CO".')
    pumas: list[str] | None = Field(
        default=None,
        description="Optional list of PUMA codes to restrict analysis to. Null = all PUMAs in state.",
    )
    service_area: ServiceAreaConfig

    @field_validator("state_fips")
    @classmethod
    def _two_digit_fips(cls, value: str) -> str:
        if not (value.isdigit() and len(value) == 2):
            raise ValueError(f"state_fips must be a 2-digit string, got {value!r}")
        return value

    @field_validator("state_abbr")
    @classmethod
    def _two_letter_abbr(cls, value: str) -> str:
        if not (value.isalpha() and len(value) == 2):
            raise ValueError(f"state_abbr must be a 2-letter string, got {value!r}")
        return value.upper()


class VintagesConfig(_StrictModel):
    acs_year: int = Field(
        description="ACS endpoint year used for tract household counts and B19001."
    )
    acs_span: Literal[1, 5] = Field(default=5, description="ACS span (1-year or 5-year).")
    pums_year: int = Field(description="PUMS sample year matched by the PUMS ZIPs.")
    pums_span: Literal[1, 5] = Field(
        default=1, description="PUMS span (1-year is the most common)."
    )
    hud_ami_fy: int = Field(description="HUD fiscal year of the AMI limits CSV.")
    tiger_year: int = Field(description="TIGER vintage for PUMA and tract shapefiles.")


class DataSourcesConfig(_StrictModel):
    hud_ami_csv: Path = Field(description="County-level HUD AMI limits CSV (long or wide).")
    smi_csv: Path | None = Field(
        default=None,
        description=(
            "Optional override for the LIHEAP SMI table (same schema as the packaged "
            "data/smi CSV). When set, used instead of the packaged file — drop in a newer "
            "IM or additional geographies without a code change."
        ),
    )
    pums_housing_zip: Path | None = Field(default=None, description="ACS PUMS housing ZIP.")
    pums_person_zip: Path | None = Field(default=None, description="ACS PUMS person ZIP.")
    tiger_puma_zip: Path | None = Field(default=None, description="TIGER PUMA shapefile ZIP.")
    tiger_tract_zip: Path | None = Field(default=None, description="TIGER tract shapefile ZIP.")
    eia861_csv: Path | None = Field(
        default=None,
        description=(
            "Optional override for the packaged EIA-861 residential table "
            "(data/eia861/eia861_residential.csv) used by calibration.electric — same "
            "schema; see docs/eia861_normalization.md. Null uses the packaged file."
        ),
    )


class CensusApiConfig(_StrictModel):
    key_env: str = Field(
        default="CENSUS_API_KEY",
        description="Name of the environment variable holding a free Census API key.",
    )
    rate_limit_sec: float = Field(
        default=0.1, ge=0.0, description="Minimum seconds between consecutive API calls."
    )


class ThresholdsConfig(_StrictModel):
    ami_method: Literal["blended_threshold", "expected_probability"] = "blended_threshold"
    ami_limit_scalar: float = Field(
        default=1.0, gt=0.0, description="Multiplier on the 80% AMI limit (e.g. 1.0, 1.25)."
    )
    smi_source: str = Field(
        default="auto",
        description=(
            'Which LIHEAP SMI fiscal year to use. "auto" (default) picks the packaged FY '
            "closest to (and not after) vintages.hud_ami_fy; or pin a year with "
            '"liheap_fy{YEAR}" (e.g. "liheap_fy2026").'
        ),
    )
    compute_smi: bool = Field(
        default=True,
        description=(
            "Whether to compute the <=60% SMI eligibility flag. Set false to run without SMI "
            "(e.g. for a state-year not in the packaged table); the <=60% SMI metrics then "
            "report zero."
        ),
    )
    energy_burden_threshold: float = Field(default=0.06, gt=0.0, lt=1.0)
    high_energy_burden_threshold: float = Field(default=0.10, gt=0.0, lt=1.0)
    rent_burden_threshold: float = Field(default=0.30, gt=0.0, lt=1.0)
    severe_rent_burden_threshold: float = Field(default=0.50, gt=0.0, lt=1.0)
    missing_cost_rule: Literal["zero", "nan"] = "zero"
    nonpos_income_rule: Literal["exclude", "treat_as_high"] = "exclude"
    suppression_min_unweighted_n: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def _check_burden_ordering(self) -> ThresholdsConfig:
        if self.high_energy_burden_threshold <= self.energy_burden_threshold:
            raise ValueError("high_energy_burden_threshold must exceed energy_burden_threshold")
        if self.severe_rent_burden_threshold <= self.rent_burden_threshold:
            raise ValueError("severe_rent_burden_threshold must exceed rent_burden_threshold")
        return self


class ElectricCalibrationConfig(_StrictModel):
    target_annual_bill: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Explicit target average annual residential electric bill in USD. When set, "
            "data_sources.eia861_csv is not consulted."
        ),
    )
    utility_number: int | None = Field(
        default=None,
        description=(
            "EIA utility ID selecting the row in the packaged EIA-861 table (or "
            "data_sources.eia861_csv when set)."
        ),
    )
    utility_name: str | None = Field(
        default=None,
        description="Utility name (case-insensitive) selecting the EIA-861 row.",
    )
    eia861_year: int | None = Field(
        default=None,
        description=(
            "EIA-861 data year. Null with the packaged table defaults to "
            "vintages.pums_year (the survey end-year); set explicitly to pin a year or "
            "when a custom CSV has a year column."
        ),
    )
    apply: bool = Field(
        default=False,
        description=(
            "False (default) runs the observed-vs-target diagnostic only; true rescales "
            "each paying household's electric cost by target/observed before burden is "
            "computed."
        ),
    )


class CalibrationConfig(_StrictModel):
    electric: ElectricCalibrationConfig | None = Field(
        default=None,
        description=(
            "EIA-861 electric bill normalization (LEAD-style). Omit the block to disable "
            "entirely; see docs/eia861_normalization.md."
        ),
    )


class WeightsConfig(_StrictModel):
    compute_moe: bool = True
    replicate_count: int = Field(default=80, ge=1)
    service_moe_method: Literal["replicates", "rss"] = "replicates"


class PumaTablePipelineConfig(_StrictModel):
    write_state: bool = True
    write_puma: bool = True
    export_replicates: bool = True


class ServiceAllocationPipelineConfig(_StrictModel):
    allocator: Literal["tract_household_weighted"] = "tract_household_weighted"
    build_urban_rural_split: bool = True


class EligibilityAnalysisPipelineConfig(_StrictModel):
    program_name: str = Field(
        default="PIPP",
        description="Cosmetic label for the eligibility program; appears in output column names.",
    )
    current_threshold: float = Field(default=0.06, gt=0.0, lt=1.0)
    proposed_threshold: float = Field(default=0.025, gt=0.0, lt=1.0)
    demographic_dimensions: list[str] = Field(
        default_factory=lambda: ["race", "ethnicity", "age", "tenure", "language", "hh_type"]
    )
    collapse_top_n_categories: int = Field(default=20, ge=1)
    gap_thresholds: list[float] | None = Field(
        default=None,
        description=(
            "Burden thresholds for the affordability-gap tables (annual dollars needed "
            "to bring each household down to the threshold). Null uses "
            "thresholds.energy_burden_threshold and thresholds.high_energy_burden_threshold."
        ),
    )

    @field_validator("gap_thresholds")
    @classmethod
    def _gap_thresholds_in_range(cls, value: list[float] | None) -> list[float] | None:
        if value is not None:
            if not value:
                raise ValueError("gap_thresholds must be null or a non-empty list")
            for t in value:
                if not 0.0 < t < 1.0:
                    raise ValueError(f"gap_thresholds entries must be in (0, 1), got {t}")
        return value


class FixedChargePipelineConfig(_StrictModel):
    monthly_increase: float = Field(
        default=4.00, description="Proposed monthly fixed-charge delta in USD."
    )
    apply_only_if_gas_positive: bool = Field(
        default=True,
        description="If true, the increase is applied only to households with GASP>0 (gas customers).",
    )
    scenario_sweep: list[float] = Field(
        default_factory=lambda: [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 5.47, 7.5, 10.0, 15.0]
    )
    near_burden_margins_pp: list[float] = Field(
        default_factory=lambda: [0.25, 0.50, 1.00],
        description="Percentage-point bands below the burden threshold to count `near burden`.",
    )
    delta_thresholds_pp: list[float] = Field(
        default_factory=lambda: [0.25, 0.50, 1.00],
        description="Percentage-point burden-increase thresholds for the sensitivity table.",
    )


class ReportingPipelineConfig(_StrictModel):
    income_bins_source: Literal["B19001"] = "B19001"
    figures: list[
        Literal[
            "income_comparison",
            "regressivity_curve",
            "scenario_sweep",
            "waterfall",
            "demographic_bars",
            "burden_bands",
            "choropleth",
        ]
    ] = Field(
        default_factory=lambda: [
            "income_comparison",
            "regressivity_curve",
            "scenario_sweep",
            "waterfall",
            "demographic_bars",
            "burden_bands",
            "choropleth",
        ]
    )


class PipelinesConfig(_StrictModel):
    puma_table: PumaTablePipelineConfig = Field(default_factory=PumaTablePipelineConfig)
    service_allocation: ServiceAllocationPipelineConfig = Field(
        default_factory=ServiceAllocationPipelineConfig
    )
    eligibility_analysis: EligibilityAnalysisPipelineConfig = Field(
        default_factory=EligibilityAnalysisPipelineConfig
    )
    fixed_charge: FixedChargePipelineConfig = Field(default_factory=FixedChargePipelineConfig)
    reporting: ReportingPipelineConfig = Field(default_factory=ReportingPipelineConfig)


class Config(_StrictModel):
    """Validated top-level configuration for an energy-equity run."""

    project: ProjectConfig
    geography: GeographyConfig
    vintages: VintagesConfig
    data_sources: DataSourcesConfig
    census_api: CensusApiConfig = Field(default_factory=CensusApiConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    weights: WeightsConfig = Field(default_factory=WeightsConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    pipelines: PipelinesConfig = Field(default_factory=PipelinesConfig)

    @model_validator(mode="after")
    def _check_electric_calibration_target(self) -> Config:
        electric = self.calibration.electric
        if (
            electric is not None
            and electric.target_annual_bill is None
            and electric.utility_number is None
            and electric.utility_name is None
        ):
            raise ValueError(
                "calibration.electric requires target_annual_bill, or utility_number/"
                "utility_name to look up the EIA-861 average bill"
            )
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        """Load and validate a YAML config file."""
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Config YAML at {path} must be a mapping at the top level.")
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Config:
        """Validate a config dict (e.g. loaded from YAML or built in tests)."""
        return cls.model_validate(data)
