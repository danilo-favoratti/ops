"""Settings precedence, validation, and reproducible configuration hashes."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from opsiu.settings import Settings, settings_hash

pytestmark = pytest.mark.unit
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def test_shipped_defaults_match_builtin_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = Settings()
    monkeypatch.setenv("OPSIU_CONFIG", str(PROJECT_ROOT / "opsiu.toml"))

    settings = Settings()

    assert settings == builtin
    assert (settings.years.from_, settings.years.to) == (2023, 2026)
    assert settings.sampling.per_rule == 20
    assert settings.paths.data_dir == Path("../data")
    assert settings.paths.db_path == Path("../data/opsiu.duckdb")
    assert settings.paths.runs_dir == Path("runs")
    assert settings.paths.external_dir == Path("../data/external")
    assert settings.agents.live_env_var == "AGENTRIG_RUN_LIVE"
    assert settings.agents.max_calls_per_run == 20


def test_env_overrides_toml_and_preserves_siblings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "opsiu.toml").write_text(
        "[years]\nfrom = 2020\nto = 2028\n"
        '[agents]\nmodel = "scripted-test"\nmax_calls_per_run = 7\n'
        '[paths]\ndata_dir = "input"\nruns_dir = "output"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPSIU_YEARS__FROM", "2022")
    monkeypatch.setenv("OPSIU_AGENTS__MAX_CALLS_PER_RUN", "3")
    monkeypatch.setenv("OPSIU_PATHS__RUNS_DIR", "other-output")

    settings = Settings()

    assert (settings.years.from_, settings.years.to) == (2022, 2028)
    assert settings.agents.max_calls_per_run == 3
    assert settings.agents.model == "scripted-test"
    assert settings.paths.data_dir == Path("input")
    assert settings.paths.runs_dir == Path("other-output")


def test_config_path_is_selected_for_each_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "opsiu.toml").write_text("[sampling]\nper_rule = 1\n", encoding="utf-8")
    custom_config = tmp_path / "custom.toml"
    custom_config.write_text("[sampling]\nper_rule = 9\n", encoding="utf-8")

    assert Settings().sampling.per_rule == 1
    monkeypatch.setenv("OPSIU_CONFIG", str(custom_config))
    assert Settings().sampling.per_rule == 9
    monkeypatch.delenv("OPSIU_CONFIG")
    assert Settings().sampling.per_rule == 1


def test_explicit_missing_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSIU_CONFIG", "missing.toml")

    with pytest.raises(FileNotFoundError):
        Settings()


def test_hash_is_canonical_and_changes_with_effective_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "opsiu.toml"
    config.write_text(
        '[years]\nfrom = 2023\nto = 2026\n[review]\nreviewer = "João"\n',
        encoding="utf-8",
    )
    first = Settings()
    config.write_text(
        '[review]\nreviewer = "João"\n[years]\nto = 2026\nfrom = 2023\n',
        encoding="utf-8",
    )

    assert first.settings_hash() == settings_hash()
    expected = json.dumps(
        first.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert settings_hash(first) == hashlib.sha256(expected.encode("utf-8")).hexdigest()

    monkeypatch.setenv("OPSIU_SAMPLING__PER_RULE", "1")
    assert settings_hash() != first.settings_hash()


@pytest.mark.parametrize(
    "contents",
    [
        "[years]\nfrom = 2027\nto = 2026\n",
        "[sampling]\nper_rule = 0\n",
        "[agents]\nmax_calls_per_run = -1\n",
        '[review]\nreviewer = " "\n',
        '[external]\nreceita_url = "not-a-url"\n',
        "[sampling]\nper_rul = 4\n",
    ],
)
def test_invalid_configuration_is_rejected(contents: str, tmp_path: Path) -> None:
    (tmp_path / "opsiu.toml").write_text(contents, encoding="utf-8")

    with pytest.raises(ValidationError):
        Settings()
