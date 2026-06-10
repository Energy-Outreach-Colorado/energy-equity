"""Unit tests for the plotly figure builders (skipped without the viz extra)."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

pytest.importorskip("plotly")
import plotly.graph_objects as go  # noqa: E402

from energy_equity.reporting import figures as F  # noqa: E402


def test_fig_income_comparison_returns_figure() -> None:
    df = pd.DataFrame(
        {
            "bin_label": ["<$10k", "$10-15k"],
            "service_share": [0.2, 0.3],
            "statewide_share": [0.1, 0.25],
        }
    )
    assert isinstance(F.fig_income_comparison(df), go.Figure)


def test_fig_regressivity_curve_returns_figure() -> None:
    df = pd.DataFrame({"bin_label": ["<$10k", "$10-15k"], "increase_as_pct_of_income": [0.5, 0.3]})
    assert isinstance(F.fig_regressivity_curve(df), go.Figure)


def test_fig_scenario_sweep_returns_figure() -> None:
    df = pd.DataFrame(
        {
            "segment": ["all", "all"],
            "population": ["All households", "All households"],
            "monthly_fixed_charge_increase": [0.0, 5.0],
            "newly_energy_burdened_households": [0.0, 100.0],
            "newly_highly_energy_burdened_households": [0.0, 25.0],
        }
    )
    assert isinstance(F.fig_scenario_sweep(df), go.Figure)


def test_fig_burden_waterfall_returns_figure() -> None:
    df = pd.DataFrame(
        {
            "segment": ["all"],
            "population": ["All households"],
            "baseline_energy_burdened_households": [1000.0],
            "newly_energy_burdened_households": [150.0],
        }
    )
    assert isinstance(F.fig_burden_waterfall(df), go.Figure)


def test_fig_demographic_bars_returns_figure() -> None:
    df = pd.DataFrame(
        {
            "head_race": ["White alone", "Two or more races"],
            "current_eligible_households": [500.0, 100.0],
            "proposed_eligible_households": [900.0, 220.0],
        }
    )
    assert isinstance(F.fig_demographic_bars(df, category_col="head_race"), go.Figure)


def test_fig_burden_bands_returns_figure() -> None:
    df = pd.DataFrame(
        {
            "segment": ["all", "all"],
            "burden_band": ["<2.5% burden", ">=6% burden"],
            "share_of_low_income": [0.4, 0.2],
        }
    )
    fig = F.fig_burden_bands(df, band_col="burden_band", share_col="share_of_low_income")
    assert isinstance(fig, go.Figure)


def test_fig_puma_choropleth_returns_figure() -> None:
    gdf = gpd.GeoDataFrame(
        {"PUMACE20": ["00800", "00900"]},
        geometry=[box(0, 0, 2, 1), box(2, 0, 4, 1)],
        crs="EPSG:5070",
    )
    values = pd.DataFrame(
        {"PUMA": ["00800", "00900"], "current_eligible_households": [1200.0, 800.0]}
    )
    fig = F.fig_puma_choropleth(gdf, values, value_col="current_eligible_households", title="test")
    assert isinstance(fig, go.Figure)
