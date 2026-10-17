from typing import Any

from pydantic import BaseModel, Field


class Entity(BaseModel):
    id: str
    kind: str
    uri: str | None = None
    types: list[str] = Field(default_factory=list)
    title: dict[str, list[str]] = Field(default_factory=dict)
    description: dict[str, list[str]] = Field(default_factory=dict)
    issued: str | None = None
    modified: str | None = None
    identifiers: list[str] = Field(default_factory=list)
    homepages: list[str] = Field(default_factory=list)
    extra_properties: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
