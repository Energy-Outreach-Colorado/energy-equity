"""TIGER and service-area shapefile readers with explicit CRS handling.

Geo functions in this package follow a strict CRS discipline:

- Spatial joins (assignments, point-in-polygon, attribute lookups) use **EPSG:4326**.
- Area-weighted operations (intersections, dissolves, area fractions) use **EPSG:5070**
  (NAD83 / Conus Albers), an equal-area projection appropriate for the continental US.

Functions accept input in any CRS and call `.to_crs(...)` explicitly. Callers should
never assume an unprojected GeoDataFrame is already in the correct frame.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import geopandas as gpd

CRS_GEOGRAPHIC = "EPSG:4326"
CRS_EQUAL_AREA = "EPSG:5070"


def read_geofile(path: str | Path) -> gpd.GeoDataFrame:
    """Read a vector file (shapefile, GeoJSON, GeoPackage, or zipped shapefile) into a GeoDataFrame.

    For .zip inputs, GDAL's ``zip://`` virtual filesystem handles the archive directly.
    """
    p = Path(path)
    if p.suffix.lower() == ".zip":
        return gpd.read_file(f"zip://{p}")
    return gpd.read_file(p)


def read_tiger_zip(zip_path: str | Path) -> gpd.GeoDataFrame:
    """Read a TIGER shapefile ZIP. Thin wrapper over read_geofile for naming clarity."""
    return read_geofile(zip_path)


def get_first_existing_col(
    gdf: gpd.GeoDataFrame, candidates: Iterable[str], *, description: str = "column"
) -> str:
    """Return the first column name in `candidates` that exists on `gdf`.

    TIGER column names drift slightly between vintages (e.g. PUMACE10 vs PUMACE20 vs PUMACE5);
    callers pass a list of plausible names and let this helper pick.
    """
    for name in candidates:
        if name in gdf.columns:
            return name
    raise KeyError(
        f"None of the candidate {description} columns are present. "
        f"Looked for {list(candidates)} in {list(gdf.columns)[:20]}{'...' if len(gdf.columns) > 20 else ''}."
    )


def dissolve_service_area(gdf: gpd.GeoDataFrame, *, crs: str = CRS_EQUAL_AREA) -> gpd.GeoDataFrame:
    """Dissolve all features in `gdf` into a single polygon (or multipolygon).

    The dissolve is performed in `crs` (equal-area by default) to avoid distortion in area
    calculations downstream. The returned GeoDataFrame has one row and the same CRS.
    """
    projected = gdf.to_crs(crs)
    merged = projected.unary_union
    return gpd.GeoDataFrame(geometry=[merged], crs=crs)
