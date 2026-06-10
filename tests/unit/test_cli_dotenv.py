"""The CLI should auto-load a .env from the working directory."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from energy_equity.cli import app


def test_cli_loads_dotenv_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Use a probe var (not CENSUS_API_KEY, which conftest pins) to assert .env was read.
    (tmp_path / ".env").write_text("EE_DOTENV_PROBE=loaded_ok\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EE_DOTENV_PROBE", raising=False)

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0

    try:
        assert os.environ.get("EE_DOTENV_PROBE") == "loaded_ok"
    finally:
        os.environ.pop("EE_DOTENV_PROBE", None)


def test_cli_dotenv_does_not_override_real_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A value already in the environment wins over the .env file (override=False).
    (tmp_path / ".env").write_text("EE_DOTENV_PROBE=from_dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EE_DOTENV_PROBE", "from_shell")

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert os.environ["EE_DOTENV_PROBE"] == "from_shell"
