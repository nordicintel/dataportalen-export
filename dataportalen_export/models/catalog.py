from typing import Literal

from pydantic import Field

from dataportalen_export.models.entity import Entity


class Catalog(Entity):
    kind: Literal["catalogs"] = "catalogs"
    context_ids: list[str] = Field(default_factory=list)
