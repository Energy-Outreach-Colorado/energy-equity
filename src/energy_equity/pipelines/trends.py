"""Multi-year trend tables and figures from completed per-year runs.

A single run covers one PUMS vintage. To show how affordability metrics move over
time, this pipeline compares the outputs of several completed runs, one per survey
year, listed in `pipelines.trends.runs`. It reads each run's `affordability_gap.csv`
and the service-area `{label}_totals.csv` / `{label}_rates.csv`, stacks them into
long tables with a `year` column, computes year-over-year changes, and renders trend
figures with 90% margin-of-error bands.

Writes (to the current config's output_dir):
  - trends_affordability_gap.csv   gap metrics per year, segment, population, threshold
  - trends_energy_burden.csv       burdened households and burden rate per year, segment
  - trends_deltas.csv              year-over-year changes with root-sum-of-squares MOEs
  - figures/*.png                  gap_trend, households_in_gap_trend, burden_rate_trend

Dollar amounts stay in each survey year's own dollars (`dollar_basis` column set to
"nominal_survey_year"). Change MOEs assume independent samples, which holds for
1-year PUMS vintages; do not feed overlapping 5-year runs to this pipeline.

Each run's bill-normalization treatment is recorded per fuel in the
`electric_calibration` and `gas_calibration` columns (calibrated / diagnostic /
uncalibrated, detected from the calibration CSVs in the run directory). Mixing bases
across years draws a warning: a level correction applied to only some years
manufactures a fake trend.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
from loguru import logger

from ..config import Config, TrendRunConfig
from ..paths import ensure_dir

BURDEN_RATE_NAME = "pct_energy_burdened_of_valid"

GAP_DELTA_METRICS: tuple[tuple[str, str | None], ...] = (
    ("households_in_gap", "households_in_gap_moe90"),
    ("total_gap_dollars", "total_gap_dollars_moe90"),
)
BURDEN_DELTA_METRICS: tuple[tuple[str, str | None], ...] = (
    ("energy_burdened_households", "energy_burdened_households_moe90"),
    ("energy_burden_rate", "energy_burden_rate_moe90"),
)


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    logger.info("wrote {}", path.resolve())
    return path


def _sorted_runs(cfg: Config) -> list[TrendRunConfig]:
    runs = list(cfg.pipelines.trends.runs)
    if not runs:
        raise ValueError("pipelines.trends.runs is empty; list one completed run per survey year")
    years = [run.year for run in runs]
    if len(set(years)) != len(years):
        raise ValueError(f"pipelines.trends.runs has duplicate years: {sorted(years)}")
    return sorted(runs, key=lambda run: run.year)


def _calibration_basis(run_dir: Path, fuel: str) -> str:
    """How a run treated one fuel: calibrated, diagnostic (computed, not applied), or uncalibrated."""
    path = run_dir / f"calibration_{fuel}.csv"
    if not path.exists():
        return "uncalibrated"
    df = pd.read_csv(path)
    if len(df) and bool(df.iloc[0].get("applied", False)):
        return "calibrated"
    return "diagnostic"


def _calibration_bases(runs: list[TrendRunConfig]) -> pd.DataFrame:
    rows = [
        {
            "year": int(run.year),
            "electric_calibration": _calibration_basis(Path(run.output_dir), "electric"),
            "gas_calibration": _calibration_basis(Path(run.output_dir), "gas"),
        }
        for run in runs
    ]
    return pd.DataFrame(rows)


def _warn_on_mixed_bases(bases: pd.DataFrame) -> None:
    for col in ("electric_calibration", "gas_calibration"):
        if bases[col].nunique() > 1:
            detail = ", ".join(f"{r.year}={getattr(r, col)}" for r in bases.itertuples())
            logger.warning(
                "trend runs mix {} bases ({}); a level correction applied to some years "
                "but not others manufactures a fake trend. Calibrate all years or none.",
                col.replace("_", " "),
                detail,
            )


def _resolve_service_label(run_dir: Path, override: str | None) -> str | None:
    if override is not None:
        return override
    matches = sorted(run_dir.glob("*_rates.csv"))
    if len(matches) == 1:
        return matches[0].name.removesuffix("_rates.csv")
    if len(matches) > 1:
        names = [m.name for m in matches]
        raise ValueError(
            f"{run_dir} contains multiple allocation rate files {names}; set "
            "pipelines.trends.runs[].service_label to pick one"
        )
    return None


def _load_gap_years(runs: list[TrendRunConfig]) -> pd.DataFrame:
    frames = []
    for run in runs:
        path = Path(run.output_dir) / "affordability_gap.csv"
        if not path.exists():
            logger.warning(
                "{} missing affordability_gap.csv; gap trend skips {}", path.parent, run.year
            )
            continue
        df = pd.read_csv(path)
        df.insert(0, "year", int(run.year))
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["dollar_basis"] = "nominal_survey_year"
    return out


def _load_burden_years(runs: list[TrendRunConfig]) -> pd.DataFrame:
    rows = []
    for run in runs:
        run_dir = Path(run.output_dir)
        label = _resolve_service_label(run_dir, run.service_label)
        if label is None:
            logger.warning("{} has no *_rates.csv; burden trend skips {}", run_dir, run.year)
            continue
        totals = pd.read_csv(run_dir / f"{label}_totals.csv")
        rates = pd.read_csv(run_dir / f"{label}_rates.csv")
        burden_rates = rates[rates["rate"] == BURDEN_RATE_NAME].set_index("segment")
        for _, trow in totals.iterrows():
            segment = trow["segment"]
            row = {
                "year": int(run.year),
                "segment": segment,
                "service": label,
                "energy_burdened_households": trow.get("hh_eb_w_in_service"),
                "energy_burdened_households_moe90": trow.get("hh_eb_w_in_service_moe90"),
                "energy_burden_rate": float("nan"),
                "energy_burden_rate_moe90": float("nan"),
            }
            if segment in burden_rates.index:
                row["energy_burden_rate"] = burden_rates.loc[segment, "estimate"]
                row["energy_burden_rate_moe90"] = burden_rates.loc[segment, "moe90"]
            rows.append(row)
    return pd.DataFrame(rows)


def build_year_deltas(
    df: pd.DataFrame,
    *,
    table: str,
    id_cols: list[str],
    metrics: tuple[tuple[str, str | None], ...],
) -> pd.DataFrame:
    """Consecutive-year changes per identity group, with RSS margins of error.

    The MOE of a change between two independent samples is
    sqrt(moe_a^2 + moe_b^2); it is NaN when either year's MOE is missing.
    """
    rows = []
    for keys, group in df.groupby(id_cols, dropna=False):
        group = group.sort_values("year")
        key_values = keys if isinstance(keys, tuple) else (keys,)
        ids = dict(zip(id_cols, key_values, strict=True))
        for (_, prev), (_, curr) in zip(group.iterrows(), group.iloc[1:].iterrows(), strict=False):
            for value_col, moe_col in metrics:
                if value_col not in group.columns:
                    continue
                value_from = prev[value_col]
                value_to = curr[value_col]
                delta_moe = float("nan")
                if moe_col is not None and moe_col in group.columns:
                    moe_from = prev[moe_col]
                    moe_to = curr[moe_col]
                    if pd.notna(moe_from) and pd.notna(moe_to):
                        delta_moe = math.sqrt(float(moe_from) ** 2 + float(moe_to) ** 2)
                rows.append(
                    {
                        "table": table,
                        **ids,
                        "metric": value_col,
                        "year_from": int(prev["year"]),
                        "year_to": int(curr["year"]),
                        "value_from": value_from,
                        "value_to": value_to,
                        "delta": value_to - value_from,
                        "delta_moe90": delta_moe,
                    }
                )
    return pd.DataFrame(rows)


def run(cfg: Config) -> dict[str, pd.DataFrame]:
    """Build the trend tables and figures from the configured per-year runs."""
    output_dir = ensure_dir(cfg.project.output_dir)
    runs = _sorted_runs(cfg)

    written: dict[str, pd.DataFrame] = {}

    bases = _calibration_bases(runs)
    _warn_on_mixed_bases(bases)

    gap = _load_gap_years(runs)
    if not gap.empty:
        gap = gap.merge(bases, on="year", how="left")
        _write(gap, output_dir / "trends_affordability_gap.csv")
        written["trends_affordability_gap"] = gap

    burden = _load_burden_years(runs)
    if not burden.empty:
        burden = burden.merge(bases, on="year", how="left")
        _write(burden, output_dir / "trends_energy_burden.csv")
        written["trends_energy_burden"] = burden

    deltas = []
    if not gap.empty:
        deltas.append(
            build_year_deltas(
                gap,
                table="affordability_gap",
                id_cols=["segment", "population", "threshold"],
                metrics=GAP_DELTA_METRICS,
            )
        )
    if not burden.empty:
        deltas.append(
            build_year_deltas(
                burden,
                table="energy_burden",
                id_cols=["segment"],
                metrics=BURDEN_DELTA_METRICS,
            )
        )
    if deltas:
        all_deltas = pd.concat(deltas, ignore_index=True)
        _write(all_deltas, output_dir / "trends_deltas.csv")
        written["trends_deltas"] = all_deltas

    _render_figures(cfg, output_dir, gap=gap, burden=burden)
    return written


def _render_figures(
    cfg: Config, output_dir: Path, *, gap: pd.DataFrame, burden: pd.DataFrame
) -> None:
    requested = list(cfg.pipelines.trends.figures)
    if not requested:
        return
    from ..reporting import figures as F

    if not F.HAS_PLOTLY:
        logger.warning("plotly not installed (viz extra); skipping trend figures")
        return
    fig_dir = ensure_dir(output_dir / "figures")

    def _emit(fig_name: str, fname: str, build) -> None:
        if fig_name not in requested:
            return
        try:
            fig = build()
            F.save_figure(fig, fig_dir / fname)
            logger.info("wrote {}", (fig_dir / fname).resolve())
        except Exception as exc:
            logger.warning("skipping figure {}: {}", fig_name, exc)

    def _gap_slice() -> pd.DataFrame:
        threshold = gap["threshold"].min()
        populations = [p for p in ["All households", "<=80% AMI"] if p in set(gap["population"])]
        return gap[
            (gap["segment"] == "all")
            & (gap["threshold"] == threshold)
            & (gap["population"].isin(populations))
        ]

    if not gap.empty:
        threshold_pct = f"{gap['threshold'].min():.0%}"
        _emit(
            "gap_trend",
            "trend_affordability_gap.png",
            lambda: F.fig_trend_lines(
                _gap_slice(),
                value_col="total_gap_dollars",
                moe_col="total_gap_dollars_moe90",
                group_col="population",
                title=f"Affordability gap at the {threshold_pct} burden threshold",
                y_title="Annual gap (nominal survey-year dollars)",
                y_tickformat="$,.0f",
            ),
        )
        _emit(
            "households_in_gap_trend",
            "trend_households_in_gap.png",
            lambda: F.fig_trend_lines(
                _gap_slice(),
                value_col="households_in_gap",
                moe_col="households_in_gap_moe90",
                group_col="population",
                title=f"Households above the {threshold_pct} burden threshold",
                y_title="Households",
                y_tickformat=",.0f",
            ),
        )
    if not burden.empty:
        _emit(
            "burden_rate_trend",
            "trend_energy_burden_rate.png",
            lambda: F.fig_trend_lines(
                burden[burden["segment"] == "all"],
                value_col="energy_burden_rate",
                moe_col="energy_burden_rate_moe90",
                title="Energy burden rate in the service territory",
                y_title="Share of burden-valid households",
                y_tickformat=".1%",
            ),
        )
