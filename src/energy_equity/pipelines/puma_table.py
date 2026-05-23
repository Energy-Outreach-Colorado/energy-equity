"""State and PUMA-level affordability tables.

Pipeline orchestrator: load PUMS once, attach AMI/SMI thresholds, then build the
state-overall table (everything aggregated to one row), the PUMA-overall table (one row
per PUMA), and demographic cuts when requested. Writes CSVs to `cfg.project.output_dir`.

This is the first pipeline gate of the migration. Parity target:
`puma_overall.csv` from the source notebook.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Config
from ..paths import ensure_dir, resolve_cache_dir
from ..pums.prepare import HouseholdMicrodata, prepare_household_microdata
from ..tables.core import build_table
from ..tables.metrics import BASELINE_COUNT_METRICS, BASELINE_RATIO_METRICS

# Demographic cuts to build for state and PUMA aggregations. Pipelines/eligibility_analysis
# build a richer set; this baseline pipeline emits one column per dimension.
DEMOGRAPHIC_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("race", "head_race"),
    ("ethnicity", "head_ethnicity"),
    ("gender", "head_gender"),
    ("age", "head_age_group"),
    ("household_language", "household_language"),
    ("head_language", "head_language_collapsed"),
)


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    print(f"[OUT] {path.resolve()}")
    return path


def _join_puma_lookup(df: pd.DataFrame, lookup: pd.DataFrame | None) -> pd.DataFrame:
    if lookup is None or "PUMA" not in df.columns:
        return df
    out = df.merge(lookup, on="PUMA", how="left")
    front = ["PUMA"]
    if "puma_name" in out.columns:
        front.append("puma_name")
    if "primary_county" in out.columns:
        front.append("primary_county")
    if "primary_county_share" in out.columns:
        front.append("primary_county_share")
    rest = [c for c in out.columns if c not in front]
    return out[front + rest]


def run(
    cfg: Config,
    *,
    microdata: HouseholdMicrodata | None = None,
    ami80_by_puma: pd.DataFrame | None = None,
    write_demographics: bool = True,
) -> dict[str, pd.DataFrame]:
    """Build and write the state and PUMA tables.

    Pass a pre-prepared `microdata` (e.g. from a parity-test fixture) to skip the load
    step. Otherwise the function reads PUMS via `prepare_household_microdata`. Returns a
    dict mapping table name -> DataFrame. When `cfg.pipelines.puma_table.export_replicates`
    is true, also writes `puma_overall_replicates.csv.gz` and adds the in-memory matrices
    under the key `"puma_overall_replicates"` (a `dict[str, np.ndarray]`) for direct
    hand-off to the service-allocation pipeline.
    """
    if microdata is None:
        microdata = prepare_household_microdata(cfg, ami80_by_puma=ami80_by_puma)

    output_dir = ensure_dir(cfg.project.output_dir)
    resolve_cache_dir(cfg.project.cache_dir)  # ensure cache exists for downstream

    suppress = cfg.thresholds.suppression_min_unweighted_n
    common = dict(
        count_metrics=BASELINE_COUNT_METRICS,
        ratio_metrics=BASELINE_RATIO_METRICS,
        compute_moe=cfg.weights.compute_moe,
        suppress_small_n=True,
        min_unweighted_n=suppress,
        suppress_rates_only=True,
    )

    written: dict[str, pd.DataFrame | dict[str, np.ndarray]] = {}

    # State-level overall.
    df = microdata.df.copy()
    df["__state__"] = cfg.geography.state_abbr
    state_md = HouseholdMicrodata(
        df=df,
        point_weight_col=microdata.point_weight_col,
        replicate_weight_cols=microdata.replicate_weight_cols,
        state_fips=microdata.state_fips,
        pums_year=microdata.pums_year,
        puma_lookup=microdata.puma_lookup,
    )
    state_overall = build_table(state_md, ["__state__"], **common)
    state_overall = state_overall.rename(columns={"__state__": "state"})
    written["state_overall"] = state_overall
    _write(state_overall, output_dir / "state_overall.csv")

    if write_demographics:
        for name, col in DEMOGRAPHIC_DIMENSIONS:
            if col not in microdata.df.columns:
                continue
            tab = build_table(microdata, [col], **common)
            written[f"state_by_{name}"] = tab
            _write(tab, output_dir / f"state_by_{name}.csv")

    # PUMA-level overall + replicate hand-off for the service-allocation pipeline.
    return_reps = cfg.pipelines.puma_table.export_replicates
    if return_reps:
        puma_overall, rep_estimates = build_table(
            microdata, ["PUMA"], **common, return_replicates=True
        )
    else:
        puma_overall = build_table(microdata, ["PUMA"], **common)
        rep_estimates = {}
    puma_overall = _join_puma_lookup(puma_overall, microdata.puma_lookup)
    written["puma_overall"] = puma_overall
    _write(puma_overall, output_dir / "puma_overall.csv")

    if return_reps and rep_estimates:
        _write_replicates(
            puma_overall, rep_estimates, output_dir / "puma_overall_replicates.csv.gz"
        )
        written["puma_overall_replicates"] = rep_estimates  # type: ignore[assignment]

    if write_demographics:
        for name, col in DEMOGRAPHIC_DIMENSIONS:
            if col not in microdata.df.columns:
                continue
            tab = build_table(microdata, ["PUMA", col], **common)
            tab = _join_puma_lookup(tab, microdata.puma_lookup)
            written[f"puma_by_{name}"] = tab
            _write(tab, output_dir / f"puma_by_{name}.csv")

    return written


def _write_replicates(
    puma_overall: pd.DataFrame, rep_estimates: dict[str, np.ndarray], path: Path
) -> Path:
    """Flatten the replicate matrices into a long CSV with one column per metric x replicate.

    Output schema matches the notebook's `puma_overall_replicates.csv.gz`:
    `PUMA, hh_total_w_rep01..hh_total_w_rep80, hh_eb_w_rep01..rep80, ...`. Built via
    pd.concat in one shot to avoid pandas' per-insert fragmentation warning.
    """
    frames: list[pd.DataFrame] = [puma_overall[["PUMA"]].reset_index(drop=True)]
    for metric, mat in rep_estimates.items():
        cols = [f"{metric}_w_rep{j + 1:02d}" for j in range(mat.shape[1])]
        frames.append(pd.DataFrame(mat, columns=cols))
    out = pd.concat(frames, axis=1)
    out.to_csv(path, index=False, compression="gzip")
    print(f"[OUT] {path.resolve()}")
    return path
