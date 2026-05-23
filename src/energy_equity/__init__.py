"""energy-equity: ACS PUMS-driven energy burden and utility affordability analysis."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("energy-equity")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
