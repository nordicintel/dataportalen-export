from typing import Literal

from pydantic import Field

from dataportalen_export.models.entity import Entity


class Distribution(Entity):
    kind: Literal["distributions"] = "distributions"
    access_urls: list[str] = Field(default_factory=list)
    download_urls: list[str] = Field(default_factory=list)
    media_types: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    licenses: list[str] = Field(default_factory=list)
    byte_size: float | None = None
