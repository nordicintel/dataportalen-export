from typing import Literal

from dataportalen_export.models.entity import Entity


class Publisher(Entity):
    kind: Literal["publishers"] = "publishers"
