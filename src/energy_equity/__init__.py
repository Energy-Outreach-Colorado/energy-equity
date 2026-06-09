"""energy-equity: ACS PUMS-driven energy burden and utility affordability analysis."""

from importlib.metadata import PackageNotFoundError, version

from loguru import logger as _logger

# Silent by default when imported as a library: no records from energy_equity.* reach a
# sink until the CLI (or a consumer) calls configure_logging() / logger.enable(...).
_logger.disable("energy_equity")

from ._logging import configure_logging  # noqa: E402  (re-export; import after disable)

try:
    __version__ = version("energy-equity")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__", "configure_logging"]
