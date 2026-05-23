"""Synthetic geospatial fixtures for the service_allocation pipeline tests.

Builds minimal TIGER-shaped tract and PUMA layers (4 tracts, 2 PUMAs) and a service-area
polygon that overlaps part of each PUMA. Geometries are intentionally rectangular so
intersections and area ratios are trivial to verify in tests.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box


def make_geo_fixtures(tmp_dir: Path) -> dict[str, Path]:
    """Build a tract zip, PUMA zip, and service-area shapefile under `tmp_dir`.

    Layout (built directly in EPSG:5070 Conus Albers so area arithmetic is exact and
    not affected by lon/lat -> equal-area reprojection distortion):

        PUMA 00800 (left):                  PUMA 00900 (right):
        +------+------+                    +------+------+
        | T1   | T2   |                    | T3   | T4   |
        +------+------+                    +------+------+

        Service area covers the right half of T2 and the left half of T3 (no T1, no T4).
    """
    crs = "EPSG:5070"
    tract_features = [
        {"GEOID20": "08101000100", "geometry": box(0, 0, 1, 1)},
        {"GEOID20": "08101000200", "geometry": box(1, 0, 2, 1)},
        {"GEOID20": "08101000300", "geometry": box(2, 0, 3, 1)},
        {"GEOID20": "08101000400", "geometry": box(3, 0, 4, 1)},
    ]
    tracts = gpd.GeoDataFrame(tract_features, geometry="geometry", crs=crs)

    puma_features = [
        {"PUMACE20": "00800", "NAMELSAD20": "West PUMA", "geometry": box(0, 0, 2, 1)},
        {"PUMACE20": "00900", "NAMELSAD20": "East PUMA", "geometry": box(2, 0, 4, 1)},
    ]
    pumas = gpd.GeoDataFrame(puma_features, geometry="geometry", crs=crs)

    # Service area covers (1.5, 0) -> (2.5, 1): right half of T2, left half of T3.
    service = gpd.GeoDataFrame({"name": ["test_service"]}, geometry=[box(1.5, 0, 2.5, 1)], crs=crs)

    # Write to "TIGER" zips and a shapefile directory.
    tract_zip = tmp_dir / "tl_tract.zip"
    puma_zip = tmp_dir / "tl_puma.zip"
    service_dir = tmp_dir / "service"
    service_dir.mkdir(exist_ok=True)
    service_path = service_dir / "test_service.shp"

    tracts_dir = tmp_dir / "_tracts"
    tracts_dir.mkdir(exist_ok=True)
    tracts.to_file(tracts_dir / "tl_tract.shp")
    with zipfile.ZipFile(tract_zip, "w") as zf:
        for p in tracts_dir.iterdir():
            zf.write(p, arcname=p.name)

    pumas_dir = tmp_dir / "_pumas"
    pumas_dir.mkdir(exist_ok=True)
    pumas.to_file(pumas_dir / "tl_puma.shp")
    with zipfile.ZipFile(puma_zip, "w") as zf:
        for p in pumas_dir.iterdir():
            zf.write(p, arcname=p.name)

    service.to_file(service_path)
    return {"tract_zip": tract_zip, "puma_zip": puma_zip, "service_path": service_path}


def make_tract_households() -> pd.DataFrame:
    """Tract household counts that make the share math clean.

    T1=1000, T2=1000, T3=1000, T4=1000. So each PUMA has 2000 households. Service area
    overlaps half of T2 (500 hh) and half of T3 (500 hh), so each PUMA has 25% (500/2000)
    of its households in service.
    """
    return pd.DataFrame(
        {
            "tract_geoid": ["08101000100", "08101000200", "08101000300", "08101000400"],
            "households": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )
