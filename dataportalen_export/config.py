"""Validated operational settings. Paths belong to the config's repository."""

import tomllib
from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Schedule(Settings):
    enabled: bool = True
    time: str = "06:00"
    timezone: str = "Europe/Stockholm"

    @field_validator("time")
    @classmethod
    def valid_time(cls, value):
        time.fromisoformat(value)
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Unknown IANA timezone") from error
        return value


class Sources(Settings):
    dataportal: bool = True
    dataeu: bool = True
    countries: list[str] = Field(default_factory=lambda: ["se"])
    exclude_eu_catalogs: list[str] = Field(default_factory=lambda: ["oppnadata"])


class Harvest(Settings):
    concurrency: int = Field(default=8, ge=1, le=64)
    request_timeout_seconds: float = Field(default=60, gt=0)
    download_timeout_seconds: float = Field(default=600, gt=0)
    max_attempts: int = Field(default=5, ge=1, le=10)


class Logging(Settings):
    level: str = "INFO"
    http_level: str = "WARNING"

    @field_validator("level", "http_level")
    @classmethod
    def valid_level(cls, value):
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Invalid logging level")
        return value


class Output(Settings):
    directory: Path = Path("data")
    combined_jsonl: bool = True
    entity_jsonl: bool = True
    catalog_jsonl: bool = True
    sqlite: bool = True
    duckdb: bool = True
    duckdb_memory_limit_mb: int = Field(default=512, ge=128)
    parquet: bool = True
    jsonld: bool = True
    compression: Literal["xz", "gzip"] = "xz"
    compression_level: int = Field(default=9, ge=0, le=9)
    compression_threshold_mib: float = Field(default=95, gt=0, le=95)


class Config(Settings):
    schedule: Schedule = Field(default_factory=Schedule)
    sources: Sources = Field(default_factory=Sources)
    harvest: Harvest = Field(default_factory=Harvest)
    logging: Logging = Field(default_factory=Logging)
    output: Output = Field(default_factory=Output)


def load_config(path: Path | None = None) -> Config:
    path = (path or Path(__file__).resolve().parents[1] / "config.toml").resolve()
    config = Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    config.output.directory = (path.parent / config.output.directory).resolve()
    return config
