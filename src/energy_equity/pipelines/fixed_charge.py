"""Rate-impact pipeline for a fixed-charge or percentage bill increase.

Applies the configured increase to each household's burden-basis cost, recomputes burden,
and produces:

  - fixed_charge_headline_summary.csv  baseline / post / newly-burdened by segment x population
  - delta_sensitivity_summary.csv      households with burden increase >= 0.25/0.50/1.00 pp
  - near_threshold_summary.csv         households just below the threshold before and after
  - scenario_sweep.csv                 the headline metrics across alternate amounts

The scenario is layered on top of an already-prepared household microdata frame; the
eligibility_analysis-style service-area weights are applied first so all metrics are
service-territory-weighted. The output file names keep the fixed-charge prefix for
compatibility with earlier runs and the reporting figures.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from ..config import Config
from ..paths import ensure_dir
from ..pums.prepare import HouseholdMicrodata
from ..tables.scenarios import (
    INCREASE_BASE_COLUMNS,
    apply_rate_increase_to_microdata,
    build_delta_sensitivity_summary,
    build_fixed_charge_headline_summary,
    build_near_threshold_summary,
    build_scenario_sweep,
)
from ..tables.summaries import attach_service_weights_and_eligibility_flags


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    logger.info("wrote {}", path.resolve())
    return path


def run(
    cfg: Config,
    microdata: HouseholdMicrodata,
    service_shares: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Run the rate-impact analysis for the configured increase."""
    output_dir = ensure_dir(cfg.project.output_dir)
    fc = cfg.pipelines.fixed_charge
    ea = cfg.pipelines.eligibility_analysis
    amount = float(fc.percent_increase) if fc.kind == "percent" else float(fc.monthly_increase)
    increase_base_col = INCREASE_BASE_COLUMNS[fc.percent_of]

    hh = attach_service_weights_and_eligibility_flags(
        microdata.df.copy(),
        service_shares,
        energy_burden_threshold=cfg.thresholds.energy_burden_threshold,
        high_energy_burden_threshold=cfg.thresholds.high_energy_burden_threshold,
        current_threshold=ea.current_threshold,
        proposed_threshold=ea.proposed_threshold,
        weight_col=microdata.point_weight_col,
    )
    apply_rate_increase_to_microdata(
        hh,
        kind=fc.kind,
        amount=amount,
        apply_only_if_gas_positive=fc.apply_only_if_gas_positive,
        base_cost_col=microdata.burden_cost_col,
        increase_base_col=increase_base_col,
        energy_burden_threshold=cfg.thresholds.energy_burden_threshold,
        high_energy_burden_threshold=cfg.thresholds.high_energy_burden_threshold,
        delta_thresholds_pp=tuple(fc.delta_thresholds_pp),
        nonpos_income_rule=cfg.thresholds.nonpos_income_rule,
    )

    written: dict[str, pd.DataFrame] = {}

    headline = build_fixed_charge_headline_summary(hh)
    all_row = headline[
        (headline["segment"] == "all") & (headline["population"] == "All households")
    ]
    if len(all_row):
        logger.info(
            "{} increase of {}: weighted mean monthly increase among households it applies to ${:.2f}, "
            "newly burdened {:.0f} households",
            fc.kind,
            amount,
            float(all_row["avg_monthly_increase_among_applies"].iloc[0]),
            float(all_row["newly_energy_burdened_households"].iloc[0]),
        )
    _write(headline, output_dir / "fixed_charge_headline_summary.csv")
    written["fixed_charge_headline_summary"] = headline

    sens = build_delta_sensitivity_summary(hh, delta_thresholds_pp=tuple(fc.delta_thresholds_pp))
    _write(sens, output_dir / "delta_sensitivity_summary.csv")
    written["delta_sensitivity_summary"] = sens

    near = build_near_threshold_summary(
        hh,
        threshold=cfg.thresholds.energy_burden_threshold,
        margins_pp=tuple(fc.near_burden_margins_pp),
    )
    _write(near, output_dir / "near_threshold_summary.csv")
    written["near_threshold_summary"] = near

    sweep = build_scenario_sweep(
        hh,
        amounts=tuple(fc.scenario_sweep),
        kind=fc.kind,
        apply_only_if_gas_positive=fc.apply_only_if_gas_positive,
        base_cost_col=microdata.burden_cost_col,
        increase_base_col=increase_base_col,
        energy_burden_threshold=cfg.thresholds.energy_burden_threshold,
        high_energy_burden_threshold=cfg.thresholds.high_energy_burden_threshold,
        nonpos_income_rule=cfg.thresholds.nonpos_income_rule,
    )
    _write(sweep, output_dir / "scenario_sweep.csv")
    written["scenario_sweep"] = sweep

    return written
