"""Quota margin settings preserve exact values and reject invalid tolerances."""

import os
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from opsiu.settings import Settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def test_margin_default_toml_and_exact_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().rules.quota_margin_percent == Decimal("5")
    (tmp_path / "opsiu.toml").write_text(
        '[rules]\nquota_margin_percent = "2.75"\n',
        encoding="utf-8",
    )
    from_file = Settings()
    assert from_file.rules.quota_margin_percent == Decimal("2.75")
    monkeypatch.setenv("OPSIU_RULES__QUOTA_MARGIN_PERCENT", "0.1234567890123456789")
    from_environment = Settings()
    assert from_environment.rules.quota_margin_percent == Decimal(
        "0.1234567890123456789"
    )
    assert from_environment.settings_hash() != from_file.settings_hash()


@pytest.mark.parametrize("margin", ["-0.01", "NaN", "Infinity", "-Infinity", "bad"])
def test_invalid_quota_margin_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    margin: str,
) -> None:
    monkeypatch.setenv("OPSIU_RULES__QUOTA_MARGIN_PERCENT", margin)
    with pytest.raises(ValidationError):
        Settings()


def test_zero_margin_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSIU_RULES__QUOTA_MARGIN_PERCENT", "0")
    assert Settings().rules.quota_margin_percent == Decimal("0")
