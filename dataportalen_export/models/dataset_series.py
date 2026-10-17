from typing import Literal

from dataportalen_export.models.entity import Entity


class DatasetSeries(Entity):
    kind: Literal["dataset_series"] = "dataset_series"
