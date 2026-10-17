from typing import Any

from pydantic import BaseModel, Field


class SourceRecord(BaseModel):
    id: str
    source: str
    native_id: str
    scope: str
    root_uri: str
    stamp: str
    digest: str
    fetched_at: str
    stale: bool = False
    rdf: str
    entities: list[dict[str, Any]]
    links: list[tuple[str, str, str]]
    catalog_ids: list[str] = Field(default_factory=list)
