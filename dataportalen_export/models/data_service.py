from typing import Literal

from pydantic import Field

from dataportalen_export.models.entity import Entity


class DataService(Entity):
    kind: Literal["data_services"] = "data_services"
    endpoint_urls: list[str] = Field(default_factory=list)
    endpoint_descriptions: list[str] = Field(default_factory=list)
