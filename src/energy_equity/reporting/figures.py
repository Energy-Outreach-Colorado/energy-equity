"""Plotly figure builders for the reporting pipeline.

Pure functions: a DataFrame (and, for the map, a GeoDataFrame) in → a plotly ``Figure``
out. The pipeline calls :func:`save_figure` to render PNGs via kaleido.

Plotly is an optional dependency (the ``viz`` extra). It is imported behind ``HAS_PLOTLY``
so importing the package without ``viz`` stays safe — callers check ``HAS_PLOTLY`` (the
reporting pipeline does) and skip figures with a warning when it is False.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

try:
    import plotly.express as px
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:  # pragma: no cover - exercised only without the viz extra
    HAS_PLOTLY = False

if TYPE_CHECKING:
    import geopandas as gpd
    import plotly.graph_objects as go


_PNG_WIDTH = 1000
_PNG_HEIGHT = 600
_PNG_SCALE = 2


def save_figure(fig: go.Figure, path: str | Path) -> Path:
    """Write `fig` to `path` as a PNG (via kaleido). Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(str(path), width=_PNG_WIDTH, height=_PNG_HEIGHT, scale=_PNG_SCALE)
    return path


# ---- Income distribution -------------------------------------------------------------


def fig_income_comparison(comparison: pd.DataFrame) -> go.Figure:
    """Grouped bar: service-area vs statewide household share by B19001 income bin."""
    df = comparison.copy()
    fig = go.Figure()
    fig.add_bar(x=df["bin_label"], y=df["service_share"], name="Service area")
    fig.add_bar(x=df["bin_label"], y=df["statewide_share"], name="Statewide")
    fig.update_layout(
        title="Household income distribution: service area vs statewide",
        xaxis_title="Household income",
        yaxis_title="Share of households",
        yaxis_tickformat=".0%",
        barmode="group",
        legend_title_text="",
    )
    fig.update_xaxes(tickangle=-45)
    return fig


def fig_regressivity_curve(regressivity: pd.DataFrame) -> go.Figure:
    """Line: the annual fixed-charge increase as a percent of income, by income bin.

    A flat dollar increase is a larger share of income for lower-income households — the
    downward slope is the regressivity story.
    """
    df = regressivity.copy()
    fig = go.Figure()
    fig.add_scatter(
        x=df["bin_label"],
        y=df["increase_as_pct_of_income"],
        mode="lines+markers",
        name="Increase as % of income",
    )
    fig.update_layout(
        title="Fixed-charge increase as a share of household income",
        xaxis_title="Household income",
        yaxis_title="Annual increase as % of income",
        yaxis_ticksuffix="%",
    )
    fig.update_xaxes(tickangle=-45)
    return fig


# ---- Fixed-charge scenario -----------------------------------------------------------


def fig_scenario_sweep(
    sweep: pd.DataFrame, *, segment: str = "all", population: str = "All households"
) -> go.Figure:
    """Line: newly energy-burdened households vs the monthly fixed-charge increase."""
    df = sweep[(sweep["segment"] == segment) & (sweep["population"] == population)].sort_values(
        "monthly_fixed_charge_increase"
    )
    fig = go.Figure()
    fig.add_scatter(
        x=df["monthly_fixed_charge_increase"],
        y=df["newly_energy_burdened_households"],
        mode="lines+markers",
        name="Newly energy burdened",
    )
    fig.add_scatter(
        x=df["monthly_fixed_charge_increase"],
        y=df["newly_highly_energy_burdened_households"],
        mode="lines+markers",
        name="Newly highly energy burdened",
    )
    fig.update_layout(
        title=f"Newly burdened households vs monthly fixed-charge increase ({population})",
        xaxis_title="Monthly fixed-charge increase ($)",
        yaxis_title="Households",
    )
    return fig


def fig_burden_waterfall(
    headline: pd.DataFrame, *, segment: str = "all", population: str = "All households"
) -> go.Figure:
    """Waterfall: baseline energy-burdened → newly burdened → post-increase total."""
    row = headline[(headline["segment"] == segment) & (headline["population"] == population)]
    baseline = float(row["baseline_energy_burdened_households"].iloc[0])
    newly = float(row["newly_energy_burdened_households"].iloc[0])
    fig = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=["absolute", "relative", "total"],
            x=["Baseline burdened", "Newly burdened", "After increase"],
            y=[baseline, newly, baseline + newly],
            connector={"line": {"color": "rgb(150,150,150)"}},
        )
    )
    fig.update_layout(
        title=f"Energy-burdened households before and after the increase ({population})",
        yaxis_title="Households",
    )
    return fig


# ---- Eligibility demographics + burden bands -----------------------------------------


def fig_demographic_bars(demographics: pd.DataFrame, *, category_col: str) -> go.Figure:
    """Grouped bar: current vs proposed eligible households by a demographic category."""
    df = demographics.copy()
    fig = go.Figure()
    fig.add_bar(x=df[category_col], y=df["current_eligible_households"], name="Current eligible")
    fig.add_bar(x=df[category_col], y=df["proposed_eligible_households"], name="Proposed eligible")
    fig.update_layout(
        title=f"Eligible households by {category_col.replace('head_', '').replace('_', ' ')}",
        xaxis_title="",
        yaxis_title="Households",
        barmode="group",
        legend_title_text="",
    )
    fig.update_xaxes(tickangle=-30)
    return fig


def fig_burden_bands(
    bands: pd.DataFrame, *, band_col: str, share_col: str, segment: str = "all"
) -> go.Figure:
    """Bar: household share by burden band for one segment."""
    df = bands[bands["segment"] == segment]
    fig = go.Figure()
    fig.add_bar(x=df[band_col].astype(str), y=df[share_col], name=segment)
    fig.update_layout(
        title="Households by energy-burden band",
        xaxis_title="Burden band",
        yaxis_title="Share of households",
        yaxis_tickformat=".0%",
    )
    return fig


# ---- PUMA choropleth -----------------------------------------------------------------


def fig_puma_choropleth(
    puma_gdf: gpd.GeoDataFrame,
    values: pd.DataFrame,
    *,
    value_col: str,
    title: str,
    geo_puma_col: str = "PUMACE20",
    values_puma_col: str = "PUMA",
) -> go.Figure:
    """Choropleth of a per-PUMA value, rendered with plotly's tile-free geo (no Mapbox token).

    `puma_gdf` is a TIGER PUMA layer (any CRS; reprojected to EPSG:4326 here). `values` has a
    PUMA code column matching `geo_puma_col` and the numeric `value_col` to color by.
    """
    import json

    gdf = puma_gdf.to_crs("EPSG:4326")[[geo_puma_col, "geometry"]].copy()
    gdf[geo_puma_col] = gdf[geo_puma_col].astype(str).str.zfill(5)
    geojson = json.loads(gdf.to_json())

    vals = values.copy()
    vals[values_puma_col] = vals[values_puma_col].astype(str).str.zfill(5)

    fig = px.choropleth(
        vals,
        geojson=geojson,
        locations=values_puma_col,
        featureidkey=f"properties.{geo_puma_col}",
        color=value_col,
        color_continuous_scale="Viridis",
        scope="usa",
    )
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_layout(title=title, margin={"r": 0, "t": 40, "l": 0, "b": 0})
    return fig
