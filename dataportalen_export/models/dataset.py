from typing import Literal

from pydantic import Field

from dataportalen_export.models.entity import Entity


class Dataset(Entity):
    kind: Literal["datasets"] = "datasets"
    keywords: dict[str, list[str]] = Field(default_factory=dict)
    themes: list[str] = Field(default_factory=list)
    access_rights: list[str] = Field(default_factory=list)
