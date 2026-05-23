"""Fixed-charge rate-impact pipeline.

Applies a configurable monthly fixed-charge increase to each household's energy cost,
recomputes burden, and produces:

  - fixed_charge_headline_summary.csv  baseline / post / newly-burdened by segment x population
  - delta_sensitivity_summary.csv      households with burden increase >= 0.25/0.50/1.00 pp
  - scenario_sweep.csv                 same headline metrics across alternate monthly amounts

The scenario is layered on top of an already-prepared household microdata frame; the
eligibility_analysis-style service-area weights are applied first so all metrics are
service-territory-weighted.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..paths import ensure_dir
from ..pums.prepare import HouseholdMicrodata
from ..tables.scenarios import (
    apply_fixed_charge_to_microdata,
    build_delta_sensitivity_summary,
    build_fixed_charge_headline_summary,
    build_scenario_sweep,
)
from ..tables.summaries import attach_service_weights_and_eligibility_flags


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    print(f"[OUT] {path.resolve()}")
    return path


def run(
    cfg: Config,
    microdata: HouseholdMicrodata,
    service_shares: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Run the fixed-charge impact analysis."""
    output_dir = ensure_dir(cfg.project.output_dir)
    fc = cfg.pipelines.fixed_charge
    ea = cfg.pipelines.eligibility_analysis

    hh = attach_service_weights_and_eligibility_flags(
        microdata.df.copy(),
        service_shares,
        energy_burden_threshold=cfg.thresholds.energy_burden_threshold,
        high_energy_burden_threshold=cfg.thresholds.high_energy_burden_threshold,
        current_threshold=ea.current_threshold,
        proposed_threshold=ea.proposed_threshold,
        weight_col=microdata.point_weight_col,
    )
    apply_fixed_charge_to_microdata(
        hh,
        monthly_increase=fc.monthly_increase,
        apply_only_if_gas_positive=fc.apply_only_if_gas_positive,
        energy_burden_threshold=cfg.thresholds.energy_burden_threshold,
        high_energy_burden_threshold=cfg.thresholds.high_energy_burden_threshold,
        delta_thresholds_pp=tuple(fc.delta_thresholds_pp),
        nonpos_income_rule=cfg.thresholds.nonpos_income_rule,
    )

    written: dict[str, pd.DataFrame] = {}

    headline = build_fixed_charge_headline_summary(hh)
    _write(headline, output_dir / "fixed_charge_headline_summary.csv")
    written["fixed_charge_headline_summary"] = headline

    sens = build_delta_sensitivity_summary(hh, delta_thresholds_pp=tuple(fc.delta_thresholds_pp))
    _write(sens, output_dir / "delta_sensitivity_summary.csv")
    written["delta_sensitivity_summary"] = sens

    sweep = build_scenario_sweep(
        hh,
        monthly_increases=tuple(fc.scenario_sweep),
        apply_only_if_gas_positive=fc.apply_only_if_gas_positive,
        energy_burden_threshold=cfg.thresholds.energy_burden_threshold,
        high_energy_burden_threshold=cfg.thresholds.high_energy_burden_threshold,
        nonpos_income_rule=cfg.thresholds.nonpos_income_rule,
    )
    _write(sweep, output_dir / "scenario_sweep.csv")
    written["scenario_sweep"] = sweep

    return written
