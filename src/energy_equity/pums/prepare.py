"""Household microdata preparation: load PUMS, attach demographics, compute burden + thresholds.

`prepare_household_microdata(cfg)` is the canonical entry point. It returns a
`HouseholdMicrodata` dataclass that carries the cleaned DataFrame alongside the metadata
downstream functions need (which column is the point-estimate weight, which 80 columns are
the replicate weights, what vintage the data is from). Passing the dataclass into
downstream library functions eliminates the positional-arg bugs that arise when the same
DataFrame travels through 20 builder calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Config
from ..io.pums import replicate_cols
from .energy_cost import (
    apply_energy_cost_adjustment,
    apply_income_adjustment,
    compute_energy_burden_pums,
)
from .load import load_head_demographics, load_pums_households


@dataclass
class HouseholdMicrodata:
    """Prepared household-level PUMS data plus the metadata downstream functions need."""

    df: pd.DataFrame
    point_weight_col: str = "WGTP"
    replicate_weight_cols: list[str] = field(default_factory=list)
    state_fips: str = ""
    pums_year: int = 0
    has_post_scenario: bool = False
    puma_lookup: pd.DataFrame | None = None

    def __len__(self) -> int:
        return len(self.df)

    @property
    def n_replicates(self) -> int:
        return len(self.replicate_weight_cols)


# ---- Demographic labeling ---------------------------------------------------

SEX_MAP = {1: "Male", 2: "Female"}
RACE_MAP = {
    1: "White alone",
    2: "Black/African American alone",
    3: "American Indian/Alaska Native alone",
    4: "American Indian/Alaska Native alone",
    5: "American Indian/Alaska Native alone",
    6: "Asian alone",
    7: "Native Hawaiian/Other Pacific Islander alone",
    8: "Some other race alone",
    9: "Two or more races",
}
HHL_MAP = {
    1: "English only",
    2: "Spanish",
    3: "Other Indo-European",
    4: "Asian & Pacific Island",
    5: "Other",
}
TENURE_MAP = {1: "Owner w/ mortgage", 2: "Owner free & clear", 3: "Renter", 4: "No cash rent"}

UNKNOWN = "Unknown / Missing"


def add_demographic_labels(
    df: pd.DataFrame,
    *,
    age_bins: tuple[int, ...] = (0, 24, 34, 44, 54, 64, 74, 200),
    age_labels: tuple[str, ...] = ("<25", "25-34", "35-44", "45-54", "55-64", "65-74", "75+"),
) -> pd.DataFrame:
    """Map PUMS numeric codes into human-readable categorical labels.

    Operates in place. Adds: head_gender, head_ethnicity, head_race, head_age_group,
    household_language, head_language_code, tenure.
    """
    df["head_gender"] = df["SEX"].map(SEX_MAP).astype("string").fillna(UNKNOWN)

    df["head_ethnicity"] = pd.Series(np.nan, index=df.index, dtype="string")
    df.loc[df["HISP"] == 1, "head_ethnicity"] = "Not Hispanic/Latino"
    df.loc[df["HISP"].notna() & (df["HISP"] != 1), "head_ethnicity"] = "Hispanic/Latino"
    df["head_ethnicity"] = df["head_ethnicity"].fillna(UNKNOWN)

    df["head_race"] = df["RAC1P"].map(RACE_MAP).astype("string").fillna(UNKNOWN)

    df["head_age_group"] = (
        pd.cut(
            df["AGEP"],
            bins=list(age_bins),
            labels=list(age_labels),
            include_lowest=True,
            right=True,
        )
        .astype("string")
        .fillna(UNKNOWN)
    )

    df["household_language"] = df["HHL"].map(HHL_MAP).astype("string").fillna(UNKNOWN)
    df["head_language_code"] = df["LANP"].astype("Int64").astype("string").fillna(UNKNOWN)

    df["tenure"] = df["TEN"].map(TENURE_MAP).astype("string").fillna(UNKNOWN)
    return df


def collapse_top_n_categories(
    series: pd.Series,
    weights: pd.Series,
    *,
    top_n: int,
    other_label: str = "Other",
    missing_label: str = UNKNOWN,
) -> pd.Series:
    """Reduce a high-cardinality categorical to its top-N weighted categories plus an "Other" bucket.

    Used for `head_language_code` (LANP has ~1000 entries) so demographic tables stay
    readable. Categories are ranked by total weight in `weights`.
    """
    s = series.astype("string").fillna(missing_label)
    w = weights.astype(float)
    totals = pd.DataFrame({"cat": s, "w": w}).groupby("cat")["w"].sum().sort_values(ascending=False)
    top = set(totals.head(top_n).index.tolist())
    return s.where(s.isin(top), other_label)


# ---- Rent burden (PUMS-flavored) -------------------------------------------


def compute_rent_burden_pums(
    df: pd.DataFrame,
    *,
    threshold: float = 0.30,
    severe_threshold: float = 0.50,
    nonpos_income_rule: str = "exclude",
    monthly_rent_col: str = "GRNTP",
    tenure_col: str = "TEN",
    income_col: str = "income_adjusted",
    adjhsg_factor_col: str = "adjhsg_factor",
    renter_code: int = 3,
) -> pd.DataFrame:
    """Annual gross-rent / adjusted income, only for renters. Operates in place.

    Adds: annual_gross_rent_adj, rent_burden, rent_valid, rent_burdened, severe_rent_burdened.
    """
    renter = df[tenure_col] == renter_code
    grent_monthly = df[monthly_rent_col].astype(float) * df[adjhsg_factor_col]
    annual = (12.0 * grent_monthly).where(renter, np.nan)
    df["annual_gross_rent_adj"] = annual.to_numpy()

    income = df[income_col].astype(float).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        burden = np.where(income > 0, annual.to_numpy() / income, np.nan)
    nonpos = income <= 0
    if nonpos_income_rule == "exclude":
        burden = np.where(nonpos, np.nan, burden)
    elif nonpos_income_rule == "treat_as_high":
        burden = np.where(nonpos, np.inf, burden)
    else:
        raise ValueError(
            f"nonpos_income_rule must be 'exclude' or 'treat_as_high', got {nonpos_income_rule!r}"
        )

    df["rent_burden"] = burden
    df["rent_valid"] = ~pd.isna(burden)
    df["rent_burdened"] = (burden >= threshold) & ~pd.isna(burden)
    df["severe_rent_burdened"] = (burden >= severe_threshold) & ~pd.isna(burden)
    return df


# ---- Orchestrator -----------------------------------------------------------


def prepare_household_microdata(
    cfg: Config,
    *,
    pums_housing_df: pd.DataFrame | None = None,
    pums_person_df: pd.DataFrame | None = None,
    ami80_by_puma: pd.DataFrame | None = None,
    smi_table: pd.DataFrame | None = None,
    puma_lookup: pd.DataFrame | None = None,
) -> HouseholdMicrodata:
    """End-to-end household preparation: load, label, compute burden, attach AMI/SMI.

    Most arguments are optional and read from disk based on `cfg` when not provided. Tests
    and notebooks pre-load DataFrames and pass them through to avoid disk I/O.
    """
    # Late imports to avoid circular dependency between thresholds.ami and pums.prepare.
    from ..thresholds.ami import attach_ami_blended_threshold
    from ..thresholds.smi import attach_smi_statewide_threshold

    housing_zip = cfg.data_sources.pums_housing_zip
    person_zip = cfg.data_sources.pums_person_zip

    if pums_housing_df is None:
        if housing_zip is None or not Path(housing_zip).exists():
            raise FileNotFoundError(f"PUMS housing ZIP not found: {housing_zip}")
        pums_housing_df = load_pums_households(
            housing_zip,
            include_replicate_weights=cfg.weights.compute_moe,
            replicate_count=cfg.weights.replicate_count,
        )

    if pums_person_df is None:
        if person_zip is None or not Path(person_zip).exists():
            raise FileNotFoundError(f"PUMS person ZIP not found: {person_zip}")
        pums_person_df = load_head_demographics(person_zip)

    df = pums_housing_df.merge(pums_person_df, on="SERIALNO", how="left", validate="one_to_one")

    add_demographic_labels(df)

    top_n = cfg.pipelines.eligibility_analysis.collapse_top_n_categories
    df["head_language_collapsed"] = collapse_top_n_categories(
        df["head_language_code"], df["WGTP"], top_n=top_n
    )

    apply_income_adjustment(df)
    apply_energy_cost_adjustment(df, missing_cost_rule=cfg.thresholds.missing_cost_rule)
    compute_energy_burden_pums(
        df,
        threshold=cfg.thresholds.energy_burden_threshold,
        high_threshold=cfg.thresholds.high_energy_burden_threshold,
        nonpos_income_rule=cfg.thresholds.nonpos_income_rule,
    )
    compute_rent_burden_pums(
        df,
        threshold=cfg.thresholds.rent_burden_threshold,
        severe_threshold=cfg.thresholds.severe_rent_burden_threshold,
        nonpos_income_rule=cfg.thresholds.nonpos_income_rule,
    )

    if ami80_by_puma is not None:
        if cfg.thresholds.ami_method == "blended_threshold":
            df = attach_ami_blended_threshold(df, ami80_by_puma)
        elif cfg.thresholds.ami_method == "expected_probability":
            raise ValueError(
                "ami_method='expected_probability' requires puma_county_weights and county_ami_long; "
                "pass them via thresholds.ami.attach_ami_expected_probability directly."
            )

    df["is_low_income"] = pd.Series(df.get("le_80_ami")).fillna(False).astype(bool)

    attach_smi_statewide_threshold(
        df,
        state_fips=cfg.geography.state_fips,
        smi_source=cfg.thresholds.smi_source,
        table=smi_table,
    )

    rep_cols: list[str] = []
    if cfg.weights.compute_moe:
        rep_cols = [
            c for c in replicate_cols("WGTP", cfg.weights.replicate_count) if c in df.columns
        ]

    return HouseholdMicrodata(
        df=df,
        point_weight_col="WGTP",
        replicate_weight_cols=rep_cols,
        state_fips=cfg.geography.state_fips,
        pums_year=cfg.vintages.pums_year,
        has_post_scenario=False,
        puma_lookup=puma_lookup,
    )
