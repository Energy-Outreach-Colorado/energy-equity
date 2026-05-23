"""Tests for cache_dir resolution and path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from energy_equity.paths import (
    CACHE_ENV_VAR,
    default_cache_dir,
    ensure_dir,
    resolve_cache_dir,
    resolve_existing_path,
    unique_paths,
)


def test_default_cache_dir_under_app_name() -> None:
    p = default_cache_dir()
    assert "energy-equity" in str(p)


def test_resolve_cache_dir_config_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CACHE_ENV_VAR, str(tmp_path / "env"))
    override = tmp_path / "cfg"
    resolved = resolve_cache_dir(config_override=override)
    assert resolved == override
    assert resolved.is_dir()


def test_resolve_cache_dir_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "env-cache"
    monkeypatch.setenv(CACHE_ENV_VAR, str(target))
    resolved = resolve_cache_dir()
    assert resolved == target
    assert resolved.is_dir()


def test_resolve_cache_dir_default_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CACHE_ENV_VAR, raising=False)
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    resolved = resolve_cache_dir()
    assert "energy-equity" in str(resolved)
    assert resolved.is_dir()


def test_ensure_dir_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "a" / "b" / "c"
    ensure_dir(p)
    ensure_dir(p)
    assert p.is_dir()


def test_unique_paths_dedup_and_drop_none() -> None:
    out = unique_paths([Path("/a"), None, "/a", Path("/b")])
    assert out == [Path("/a"), Path("/b")]


def test_resolve_existing_path_finds_first_match(tmp_path: Path) -> None:
    missing = tmp_path / "no.txt"
    real = tmp_path / "yes.txt"
    real.write_text("x")
    result = resolve_existing_path([missing, real], description="example")
    assert result == real


def test_resolve_existing_path_raises_when_required(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_existing_path([tmp_path / "missing.txt"], description="example")


def test_resolve_existing_path_optional_returns_none(tmp_path: Path) -> None:
    out = resolve_existing_path([tmp_path / "missing.txt"], description="example", required=False)
    assert out is None


def test_resolve_existing_path_glob_fallback(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "file.csv"
    nested.parent.mkdir()
    nested.write_text("x")
    out = resolve_existing_path(
        [tmp_path / "nope.csv"],
        glob_patterns=[str(tmp_path / "**" / "*.csv")],
        description="glob example",
    )
    assert out == nested
