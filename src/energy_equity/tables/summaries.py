"""Service-area eligibility, energy-burden, and demographic summary builders.

Inputs are a household-level DataFrame that has been augmented with service-area weights
(`w_service`, `w_service_urban`, `w_service_rural`) and burden / income-qualification
flags. `attach_service_weights_and_eligibility_flags` is the canonical pre-step.

These are pure functions: DataFrame in -> DataFrame out. Used by both the
`eligibility_analysis` pipeline (PIPP-style threshold comparison) and the
`reporting` pipeline (B19001 + visual outputs).
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

SEGMENT_WEIGHTS: Mapping[str, str] = {
    "all": "w_service",
    "urban": "w_service_urban",
    "rural": "w_service_rural",
}


def safe_div(numerator: float, denominator: float) -> float:
    if denominator is None or denominator == 0 or pd.isna(denominator):
        return float("nan")
    return numerator / denominator


def _bool_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].fillna(False).astype(bool)


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    v = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce")
    mask = v.notna() & w.notna() & np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not mask.any():
        return float("nan")
    return float(np.average(v[mask], weights=w[mask]))


def _weighted_quantile(values: pd.Series, weights: pd.Series, q: float = 0.5) -> float:
    v = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce")
    mask = v.notna() & w.notna() & np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not mask.any():
        return float("nan")
    v = v[mask].to_numpy(dtype=float)
    w = w[mask].to_numpy(dtype=float)
    order = np.argsort(v)
    v, w = v[order], w[order]
    cum = np.cumsum(w)
    return float(v[np.searchsorted(cum, q * w.sum(), side="left")])


def burden_threshold_label(threshold: float) -> str:
    pct = float(threshold) * 100.0
    if np.isclose(pct, round(pct)):
        return f"{pct:.0f}%"
    return f"{pct:.1f}%"


# ---- Service-area weights + eligibility flags ----------------------------------------


def attach_service_weights_and_eligibility_flags(
    households: pd.DataFrame,
    service_shares: pd.DataFrame,
    *,
    energy_burden_threshold: float,
    high_energy_burden_threshold: float,
    current_threshold: float,
    proposed_threshold: float,
    weight_col: str = "WGTP",
) -> pd.DataFrame:
    """Merge service-area PUMA shares into the household frame and create eligibility flags.

    Adds:
      - w_service, w_service_urban, w_service_rural: per-household service-area weights
      - energy_burdened, high_energy_burdened, energy_burdened_6_to_10: universe flags
      - energy_burden_band: categorical burden band (4-way)
      - eligible_current, eligible_proposed, eligible_newly_added: PIPP-style flags
        gated by `is_low_income` (le_80_ami True) AND burden_valid
      - burden_band_within_low_income: 5-way categorical for the <=80% AMI pool
    """
    hh = households.merge(
        service_shares[["PUMA", "share_households_in_service", "urban_share_within_service"]],
        on="PUMA",
        how="left",
    )
    if "share_households_in_service" not in hh.columns:
        raise ValueError("service_shares must contain 'share_households_in_service'.")

    hh["share_households_in_service"] = pd.to_numeric(
        hh["share_households_in_service"], errors="coerce"
    ).fillna(0.0)
    urban_share = pd.to_numeric(hh["urban_share_within_service"], errors="coerce").clip(
        lower=0.0, upper=1.0
    )
    hh["urban_share_within_service"] = urban_share

    w = pd.to_numeric(hh[weight_col], errors="coerce").fillna(0.0)
    hh["w_service"] = w * hh["share_households_in_service"]
    # When urban-area data was not provided (urban_share is all-NaN), leave the urban
    # and rural weights as NaN so downstream summaries omit those segments entirely
    # rather than silently treating everything as "rural".
    if urban_share.notna().any():
        hh["w_service_urban"] = hh["w_service"] * urban_share.fillna(0.0)
        hh["w_service_rural"] = hh["w_service"] * (1.0 - urban_share.fillna(0.0))
    else:
        hh["w_service_urban"] = float("nan")
        hh["w_service_rural"] = float("nan")

    burden_valid = _bool_col(hh, "burden_valid")
    burden = pd.to_numeric(hh["energy_burden"], errors="coerce")

    hh["energy_burdened"] = burden_valid & (burden >= energy_burden_threshold)
    hh["high_energy_burdened"] = burden_valid & (burden >= high_energy_burden_threshold)
    hh["energy_burdened_6_to_10"] = hh["energy_burdened"] & (~hh["high_energy_burdened"])

    eb_label = burden_threshold_label(energy_burden_threshold)
    heb_label = burden_threshold_label(high_energy_burden_threshold)
    hh["energy_burden_band"] = np.select(
        [
            ~burden_valid,
            burden_valid & (burden < energy_burden_threshold),
            burden_valid
            & (burden >= energy_burden_threshold)
            & (burden < high_energy_burden_threshold),
            burden_valid & (burden >= high_energy_burden_threshold),
        ],
        [
            "Invalid or non-positive income",
            f"<{eb_label} burden",
            f"{eb_label} to <{heb_label} burden",
            f">={heb_label} burden",
        ],
        default="Invalid or non-positive income",
    )

    low_income = _bool_col(hh, "is_low_income")
    hh["energy_burdened_low_income"] = low_income & hh["energy_burdened"]
    hh["high_energy_burdened_low_income"] = low_income & hh["high_energy_burdened"]

    hh["eligible_current"] = low_income & burden_valid & (burden >= current_threshold)
    hh["eligible_proposed"] = low_income & burden_valid & (burden >= proposed_threshold)
    hh["eligible_newly_added"] = hh["eligible_proposed"] & (~hh["eligible_current"])

    cur_label = burden_threshold_label(current_threshold)
    prop_label = burden_threshold_label(proposed_threshold)
    hh["burden_band_within_low_income"] = np.select(
        [
            ~low_income,
            low_income & (~burden_valid),
            low_income & burden_valid & (burden < proposed_threshold),
            low_income
            & burden_valid
            & (burden >= proposed_threshold)
            & (burden < current_threshold),
            low_income & burden_valid & (burden >= current_threshold),
        ],
        [
            "Outside <=80% AMI",
            "Invalid or non-positive income",
            f"<{prop_label} burden",
            f"{prop_label} to <{cur_label} burden",
            f">={cur_label} burden",
        ],
        default="Outside <=80% AMI",
    )
    return hh


# ---- Headline summaries ---------------------------------------------------------------


def build_headline_summary(df: pd.DataFrame) -> pd.DataFrame:
    """PIPP-style eligibility comparison (current vs proposed) for each segment."""
    rows = []
    for segment, wcol in SEGMENT_WEIGHTS.items():
        w = pd.to_numeric(df[wcol], errors="coerce").fillna(0.0)
        service_households = float(w.sum())
        low_income = float(w[_bool_col(df, "is_low_income")].sum())
        current_eligible = float(w[_bool_col(df, "eligible_current")].sum())
        proposed_eligible = float(w[_bool_col(df, "eligible_proposed")].sum())
        newly_added = float(w[_bool_col(df, "eligible_newly_added")].sum())

        rows.append(
            {
                "segment": segment,
                "service_households": service_households,
                "low_income_households": low_income,
                "current_eligible_households": current_eligible,
                "proposed_eligible_households": proposed_eligible,
                "newly_added_households": newly_added,
                "absolute_increase_households": proposed_eligible - current_eligible,
                "pct_increase_vs_current": safe_div(
                    proposed_eligible - current_eligible, current_eligible
                ),
                "current_rate_among_low_income": safe_div(current_eligible, low_income),
                "proposed_rate_among_low_income": safe_div(proposed_eligible, low_income),
                "newly_added_rate_among_low_income": safe_div(newly_added, low_income),
                "current_rate_of_all_service_households": safe_div(
                    current_eligible, service_households
                ),
                "proposed_rate_of_all_service_households": safe_div(
                    proposed_eligible, service_households
                ),
                "newly_added_rate_of_all_service_households": safe_div(
                    newly_added, service_households
                ),
            }
        )
    return pd.DataFrame(rows)


def build_burden_band_summary(
    df: pd.DataFrame, *, current_threshold: float, proposed_threshold: float
) -> pd.DataFrame:
    """Distribution of <=80% AMI households across burden bands."""
    band_order = [
        "Invalid or non-positive income",
        f"<{burden_threshold_label(proposed_threshold)} burden",
        f"{burden_threshold_label(proposed_threshold)} to <{burden_threshold_label(current_threshold)} burden",
        f">={burden_threshold_label(current_threshold)} burden",
    ]
    rows = []
    low_income_mask = _bool_col(df, "is_low_income")
    for segment, wcol in SEGMENT_WEIGHTS.items():
        subset = df.loc[low_income_mask].copy()
        w = pd.to_numeric(subset[wcol], errors="coerce").fillna(0.0)
        total = float(w.sum())
        for band in band_order:
            households = float(w[subset["burden_band_within_low_income"] == band].sum())
            rows.append(
                {
                    "segment": segment,
                    "burden_band": band,
                    "households": households,
                    "share_of_low_income": safe_div(households, total),
                }
            )
    out = pd.DataFrame(rows)
    out["burden_band"] = pd.Categorical(out["burden_band"], categories=band_order, ordered=True)
    return out.sort_values(["segment", "burden_band"]).reset_index(drop=True)


def build_energy_burden_headline_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Energy-burden universe summary for each segment (independent of program thresholds)."""
    rows = []
    for segment, wcol in SEGMENT_WEIGHTS.items():
        w = pd.to_numeric(df[wcol], errors="coerce").fillna(0.0)
        service = float(w.sum())
        valid = _bool_col(df, "burden_valid")
        eb = _bool_col(df, "energy_burdened")
        heb = _bool_col(df, "high_energy_burdened")
        moderate = _bool_col(df, "energy_burdened_6_to_10")
        low_income = _bool_col(df, "is_low_income")

        valid_hh = float(w[valid].sum())
        eb_hh = float(w[eb].sum())
        heb_hh = float(w[heb].sum())
        moderate_hh = float(w[moderate].sum())
        low_income_hh = float(w[low_income].sum())
        eb_li = float(w[eb & low_income].sum())
        heb_li = float(w[heb & low_income].sum())

        rows.append(
            {
                "segment": segment,
                "service_households": service,
                "burden_valid_households": valid_hh,
                "low_income_households": low_income_hh,
                "energy_burdened_households": eb_hh,
                "energy_burdened_6_to_10_households": moderate_hh,
                "highly_energy_burdened_households": heb_hh,
                "energy_burdened_rate_of_all_service_households": safe_div(eb_hh, service),
                "highly_energy_burdened_rate_of_all_service_households": safe_div(heb_hh, service),
                "energy_burdened_rate_among_burden_valid": safe_div(eb_hh, valid_hh),
                "highly_energy_burdened_rate_among_burden_valid": safe_div(heb_hh, valid_hh),
                "energy_burdened_6_to_10_rate_among_burden_valid": safe_div(moderate_hh, valid_hh),
                "energy_burdened_low_income_households": eb_li,
                "highly_energy_burdened_low_income_households": heb_li,
                "share_energy_burdened_low_income": safe_div(eb_li, eb_hh),
                "share_highly_energy_burdened_low_income": safe_div(heb_li, heb_hh),
                "avg_energy_burden_all_burden_valid": _weighted_mean(
                    df.loc[valid, "energy_burden"], w[valid]
                ),
                "avg_energy_burden_energy_burdened": _weighted_mean(
                    df.loc[eb, "energy_burden"], w[eb]
                ),
                "avg_energy_burden_highly_energy_burdened": _weighted_mean(
                    df.loc[heb, "energy_burden"], w[heb]
                ),
                "median_energy_burden_all_burden_valid": _weighted_quantile(
                    df.loc[valid, "energy_burden"], w[valid], 0.5
                ),
                "median_energy_burden_energy_burdened": _weighted_quantile(
                    df.loc[eb, "energy_burden"], w[eb], 0.5
                ),
                "median_energy_burden_highly_energy_burdened": _weighted_quantile(
                    df.loc[heb, "energy_burden"], w[heb], 0.5
                ),
                "avg_household_income_energy_burdened": _weighted_mean(
                    df.loc[eb, "income_adjusted"], w[eb]
                ),
                "avg_household_income_highly_energy_burdened": _weighted_mean(
                    df.loc[heb, "income_adjusted"], w[heb]
                ),
                "avg_annual_energy_cost_energy_burdened": _weighted_mean(
                    df.loc[eb, "annual_energy_cost_adj"], w[eb]
                ),
                "avg_annual_energy_cost_highly_energy_burdened": _weighted_mean(
                    df.loc[heb, "annual_energy_cost_adj"], w[heb]
                ),
            }
        )
    return pd.DataFrame(rows)


def build_energy_burden_band_summary(
    df: pd.DataFrame, *, energy_burden_threshold: float, high_energy_burden_threshold: float
) -> pd.DataFrame:
    """Distribution of all service-area households across burden bands."""
    eb_label = burden_threshold_label(energy_burden_threshold)
    heb_label = burden_threshold_label(high_energy_burden_threshold)
    band_order = [
        "Invalid or non-positive income",
        f"<{eb_label} burden",
        f"{eb_label} to <{heb_label} burden",
        f">={heb_label} burden",
    ]
    rows = []
    for segment, wcol in SEGMENT_WEIGHTS.items():
        w = pd.to_numeric(df[wcol], errors="coerce").fillna(0.0)
        service = float(w.sum())
        valid_hh = float(w[_bool_col(df, "burden_valid")].sum())
        for band in band_order:
            mask = df["energy_burden_band"] == band
            households = float(w[mask].sum())
            rows.append(
                {
                    "segment": segment,
                    "energy_burden_band": band,
                    "households": households,
                    "share_of_all_service_households": safe_div(households, service),
                    "share_of_burden_valid_households": (
                        float("nan")
                        if band == "Invalid or non-positive income"
                        else safe_div(households, valid_hh)
                    ),
                }
            )
    out = pd.DataFrame(rows)
    out["energy_burden_band"] = pd.Categorical(
        out["energy_burden_band"], categories=band_order, ordered=True
    )
    return out.sort_values(["segment", "energy_burden_band"]).reset_index(drop=True)


# ---- Demographic tables ---------------------------------------------------------------


def build_demographic_table(
    df: pd.DataFrame, *, category_col: str, weight_col: str = "w_service"
) -> pd.DataFrame:
    """PIPP-eligibility breakdown by a single demographic dimension."""
    current_total = float(df.loc[_bool_col(df, "eligible_current"), weight_col].sum())
    proposed_total = float(df.loc[_bool_col(df, "eligible_proposed"), weight_col].sum())
    newly_total = float(df.loc[_bool_col(df, "eligible_newly_added"), weight_col].sum())

    rows = []
    for category, g in df.groupby(category_col, dropna=False):
        w = pd.to_numeric(g[weight_col], errors="coerce").fillna(0.0)
        low_income = float(w[_bool_col(g, "is_low_income")].sum())
        current = float(w[_bool_col(g, "eligible_current")].sum())
        proposed = float(w[_bool_col(g, "eligible_proposed")].sum())
        newly = float(w[_bool_col(g, "eligible_newly_added")].sum())
        rows.append(
            {
                category_col: category,
                "low_income_households": low_income,
                "current_eligible_households": current,
                "proposed_eligible_households": proposed,
                "newly_added_households": newly,
                "absolute_increase_households": proposed - current,
                "current_share_of_current_total": safe_div(current, current_total),
                "proposed_share_of_proposed_total": safe_div(proposed, proposed_total),
                "newly_added_share_of_newly_added_total": safe_div(newly, newly_total),
                "current_rate_among_category_low_income": safe_div(current, low_income),
                "proposed_rate_among_category_low_income": safe_div(proposed, low_income),
                "newly_added_rate_among_category_low_income": safe_div(newly, low_income),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("proposed_eligible_households", ascending=False)
        .reset_index(drop=True)
    )


def build_energy_burden_demographic_table(
    df: pd.DataFrame, *, category_col: str, weight_col: str = "w_service"
) -> pd.DataFrame:
    """Energy-burden universe breakdown by a single demographic dimension."""
    w_all = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0)
    valid_total = float(w_all[_bool_col(df, "burden_valid")].sum())
    eb_total = float(w_all[_bool_col(df, "energy_burdened")].sum())
    heb_total = float(w_all[_bool_col(df, "high_energy_burdened")].sum())
    moderate_total = float(w_all[_bool_col(df, "energy_burdened_6_to_10")].sum())

    rows = []
    for category, g in df.groupby(category_col, dropna=False):
        w = pd.to_numeric(g[weight_col], errors="coerce").fillna(0.0)
        valid = float(w[_bool_col(g, "burden_valid")].sum())
        eb = float(w[_bool_col(g, "energy_burdened")].sum())
        heb = float(w[_bool_col(g, "high_energy_burdened")].sum())
        moderate = float(w[_bool_col(g, "energy_burdened_6_to_10")].sum())
        rows.append(
            {
                category_col: category,
                "burden_valid_households": valid,
                "energy_burdened_households": eb,
                "energy_burdened_6_to_10_households": moderate,
                "highly_energy_burdened_households": heb,
                "share_of_burden_valid": safe_div(valid, valid_total),
                "share_of_energy_burdened": safe_div(eb, eb_total),
                "share_of_energy_burdened_6_to_10": safe_div(moderate, moderate_total),
                "share_of_highly_energy_burdened": safe_div(heb, heb_total),
                "energy_burdened_rate_among_category": safe_div(eb, valid),
                "highly_energy_burdened_rate_among_category": safe_div(heb, valid),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("energy_burdened_households", ascending=False)
        .reset_index(drop=True)
    )


# ---- PUMA-level rollup ----------------------------------------------------------------


def build_puma_summary(df: pd.DataFrame, *, weight_col: str = "w_service") -> pd.DataFrame:
    """Rollup of eligibility + energy-burden metrics by PUMA inside the service area."""
    rows = []
    for puma, g in df.groupby("PUMA", dropna=False):
        w = pd.to_numeric(g[weight_col], errors="coerce").fillna(0.0)
        service = float(w.sum())
        if service <= 0:
            continue
        low_income = float(w[_bool_col(g, "is_low_income")].sum())
        burden_valid = float(w[_bool_col(g, "burden_valid")].sum())
        current = float(w[_bool_col(g, "eligible_current")].sum())
        proposed = float(w[_bool_col(g, "eligible_proposed")].sum())
        newly = float(w[_bool_col(g, "eligible_newly_added")].sum())
        eb = float(w[_bool_col(g, "energy_burdened")].sum())
        heb = float(w[_bool_col(g, "high_energy_burdened")].sum())
        eb_li = float(w[_bool_col(g, "energy_burdened_low_income")].sum())
        heb_li = float(w[_bool_col(g, "high_energy_burdened_low_income")].sum())
        rows.append(
            {
                "PUMA": puma,
                "service_households": service,
                "burden_valid_households": burden_valid,
                "low_income_households": low_income,
                "current_eligible_households": current,
                "proposed_eligible_households": proposed,
                "newly_added_households": newly,
                "absolute_increase_households": proposed - current,
                "current_rate_among_low_income": safe_div(current, low_income),
                "proposed_rate_among_low_income": safe_div(proposed, low_income),
                "newly_added_rate_among_low_income": safe_div(newly, low_income),
                "energy_burdened_households": eb,
                "highly_energy_burdened_households": heb,
                "energy_burdened_low_income_households": eb_li,
                "highly_energy_burdened_low_income_households": heb_li,
                "energy_burdened_rate_among_burden_valid": safe_div(eb, burden_valid),
                "highly_energy_burdened_rate_among_burden_valid": safe_div(heb, burden_valid),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("newly_added_households", ascending=False)
        .reset_index(drop=True)
    )
