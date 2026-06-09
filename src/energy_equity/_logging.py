"""Logging configuration for energy-equity.

The package uses loguru. Because loguru is a global singleton — and importing a library
should never hijack a consumer's logging — the package is *silent by default*:
`energy_equity/__init__.py` calls `logger.disable("energy_equity")` at import time, so no
records emitted from `energy_equity.*` modules reach any sink unless something re-enables
them.

The CLI entry point calls `configure_logging(level)` to turn logging on and route it to
stderr. Library consumers who want output from a script or notebook can do the same:

    import energy_equity
    energy_equity.configure_logging("INFO")   # or loguru.logger.enable("energy_equity")
"""

from __future__ import annotations

import sys

from loguru import logger

# Level + module name are stamped by loguru, so message strings stay prefix-free.
_LEVEL_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
    "<cyan>{name}</cyan> - <level>{message}</level>"
)


def configure_logging(level: str = "INFO") -> None:
    """Enable energy_equity logs and route them to stderr at `level`.

    Removes any existing sinks first so repeated calls (e.g. across CLI invocations in the
    same process, or in tests) don't stack handlers. Diagnostics go to stderr to keep
    stdout clean for piped data output.
    """
    logger.enable("energy_equity")
    logger.remove()
    logger.add(sys.stderr, level=level, format=_LEVEL_FORMAT)


__all__ = ["configure_logging", "logger"]
