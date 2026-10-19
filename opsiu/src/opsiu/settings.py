"""Typed configuration, loaded without creating files or opening services."""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

NonemptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SettingsSection(BaseModel):
    """Reject misspelled configuration keys and keep resolved values immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PathSettings(SettingsSection):
    """Paths are relative to the working directory, including custom configs."""

    data_dir: Path = Path("../data")
    db_path: Path = Path("../data/opsiu.duckdb")
    runs_dir: Path = Path("runs")
    external_dir: Path = Path("../data/external")


class YearSettings(SettingsSection):
    from_: int = Field(default=2023, alias="from", ge=1, le=9999)
    to: int = Field(default=2026, ge=1, le=9999)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.from_ > self.to:
            raise ValueError("years.from must not exceed years.to")
        return self


class SamplingSettings(SettingsSection):
    per_rule: int = Field(default=20, ge=1)


class RuleSettings(SettingsSection):
    quota_margin_percent: Decimal = Field(
        default=Decimal("5"), ge=0, allow_inf_nan=False
    )


class AgentSettings(SettingsSection):
    model: NonemptyString = "gpt-5.6-terra"
    max_calls_per_run: int = Field(default=20, ge=0)
    live_env_var: str = Field(default="AGENTRIG_RUN_LIVE", pattern=r"^[A-Z][A-Z0-9_]*$")


class ReviewSettings(SettingsSection):
    reviewer: NonemptyString = "local"


class ExternalSettings(SettingsSection):
    tse_2024_url: HttpUrl = HttpUrl(
        "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_2024.zip"
    )
    tse_2026_url: HttpUrl = HttpUrl(
        "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_2026.zip"
    )
    receita_url: HttpUrl = HttpUrl(
        "https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9"
    )


class Settings(BaseSettings):
    """Resolve explicit values, environment, TOML, then built-in defaults."""

    model_config = SettingsConfigDict(
        env_prefix="OPSIU_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    paths: PathSettings = Field(default_factory=PathSettings)
    years: YearSettings = Field(default_factory=YearSettings)
    sampling: SamplingSettings = Field(default_factory=SamplingSettings)
    rules: RuleSettings = Field(default_factory=RuleSettings)
    agents: AgentSettings = Field(default_factory=AgentSettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)
    external: ExternalSettings = Field(default_factory=ExternalSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        configured_path = os.environ.get("OPSIU_CONFIG")
        if configured_path is not None:
            if not configured_path.strip():
                raise ValueError("OPSIU_CONFIG must name a TOML file")
            config_path = Path(configured_path).expanduser()
            if not config_path.exists():
                raise FileNotFoundError(config_path)
            if not config_path.is_file():
                raise IsADirectoryError(config_path)
        else:
            config_path = Path.cwd() / "opsiu.toml"

        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_path),
        )

    def settings_hash(self) -> str:
        """Identify the effective values, independent of TOML or JSON key order."""
        return settings_hash(self)


def settings_hash(settings: Settings | None = None) -> str:
    """Return the SHA-256 of canonical JSON containing the resolved settings."""
    resolved = settings if settings is not None else Settings()
    canonical = json.dumps(
        resolved.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
