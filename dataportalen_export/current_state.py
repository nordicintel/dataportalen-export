"""One retained version per source record; failed scopes never imply deletion."""

import gzip
import json
import lzma
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rdflib import Graph

from dataportalen_export.identity import stable_id
from dataportalen_export.models.source_record import SourceRecord
from dataportalen_export.rdf_converter import (
    LINKS,
    MODELS,
    RdfConverter,
)
from dataportalen_export.rdf_graph import graph_digest

SCHEMA_VERSION = 3


def now_iso():
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class SourceBatch:
    source: str
    http: dict[str, str] = field(default_factory=dict)
    records: dict[str, SourceRecord] = field(default_factory=dict)
    complete_scopes: set[str] = field(default_factory=set)
    seen: set[str] = field(default_factory=set)
    failures: dict[str, str] = field(default_factory=dict)
    successful_scopes: set[str] = field(default_factory=set)
    reused: int = 0
    fetched: int = 0


def make_record(
    graph: Graph,
    *,
    source: str,
    native_id: str,
    scope: str,
    root_uri: str,
    stamp: str,
    aliases=None,
    catalog_ids=None,
    catalog_id_overrides=None,
    raw=None,
) -> SourceRecord:
    record_id = stable_id("source", f"{source}:{native_id}")
    entities, links = RdfConverter().convert(
        graph,
        source=source,
        scope=record_id,
        aliases=aliases,
        catalog_ids=catalog_id_overrides,
    )
    if not entities:
        raise ValueError("RDF contains no supported described entities")
    return SourceRecord(
        id=record_id,
        source=source,
        native_id=native_id,
        scope=scope,
        root_uri=root_uri,
        stamp=stamp,
        digest=graph_digest(graph),
        fetched_at=now_iso(),
        rdf=raw or graph.serialize(format="turtle"),
        entities=[e.model_dump() for e in entities],
        links=links,
        catalog_ids=sorted(set(catalog_ids or [])),
    )


class CurrentState:
    def __init__(self, records=None, referenced_records=None):
        self.records: dict[str, SourceRecord] = records or {}
        self.referenced_records = referenced_records or {}
        self.deleted = 0

    @property
    def discovery_records(self):
        return {
            key: record
            for key, record in self.records.items()
            if key not in self.referenced_records
        }

    @classmethod
    def load(cls, directory: Path):
        paths = sorted(
            path
            for path in (directory / "source_records").rglob("*.jsonl.*")
            if path.suffix in {".xz", ".gz"}
        )
        if not paths:
            paths = [
                directory / name
                for name in ("source_records.jsonl.xz", "source_records.jsonl.gz")
                if (directory / name).exists()
            ][:1]
        records = {}
        for path in paths:
            opener = lzma.open if path.suffix == ".xz" else gzip.open
            with opener(path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        record = SourceRecord.model_validate_json(line)
                        if record.id in records:
                            raise ValueError(
                                f"Duplicate retained source record {record.id}"
                            )
                        records[record.id] = record
        manifest = directory / "manifest.json"
        referenced = (
            json.loads(manifest.read_text()).get("referenced_records", {})
            if manifest.exists()
            else {}
        )
        return cls(records, referenced)

    def apply(self, batch: SourceBatch):
        candidates = {key: self.records.pop(key) for key in self.referenced_records}
        self.referenced_records = {}
        for key, old in list(self.records.items()):
            if old.source != batch.source:
                continue
            if key in batch.records:
                continue
            if old.scope in batch.complete_scopes and key not in batch.seen:
                candidates[key] = self.records.pop(key)
                self.deleted += 1
            else:
                self.records[key] = old.model_copy(update={"stale": True})
        for key, record in batch.records.items():
            candidates.pop(key, None)
            self.records[key] = record.model_copy(update={"stale": False})
        self._retain_referenced(candidates)

    def _retain_referenced(self, candidates):
        """Retain last-known descriptions only while surviving records need them."""
        if not candidates:
            return
        entities = [
            entity for record in self.records.values() for entity in record.entities
        ]
        present = {entity["id"] for entity in entities}
        required = {
            identifier
            for record in self.records.values()
            for _, left, right in record.links
            for identifier in (left, right)
        }

        def resource_references(entity):
            return {
                value["id"]
                for values in entity["extra_properties"].values()
                for value in values
                if "id" in value
            }

        uris = {uri for entity in entities for uri in resource_references(entity)}
        selected = defaultdict(set)
        changed = True
        while changed:
            changed = False
            for record in sorted(
                candidates.values(), key=lambda r: (r.source != "dataportal", r.id)
            ):
                for entity in record.entities:
                    identifier = entity["id"]
                    if identifier in present or not (
                        identifier in required or entity.get("uri") in uris
                    ):
                        continue
                    selected[record.id].add(identifier)
                    present.add(identifier)
                    uris.update(resource_references(entity))
                    changed = True
                for table, left, right in record.links:
                    owner = right if table == "series_datasets" else left
                    if (
                        not table.startswith("catalog_")
                        and owner in selected[record.id]
                    ):
                        required.update((left, right))
        for key, identifiers in selected.items():
            if identifiers:
                self.records[key] = candidates[key].model_copy(
                    update={"stale": True, "catalog_ids": []}
                )
                self.referenced_records[key] = sorted(identifiers)

    def materialize(self):
        entities = {kind: {} for kind in MODELS}
        links, provenance = set(), set()
        records = sorted(
            self.records.values(), key=lambda r: (r.source != "dataportal", r.id)
        )
        for record in records:
            selected = self.referenced_records.get(record.id)
            for item in record.entities:
                kind, identifier = item["kind"], item["id"]
                if selected is not None and identifier not in selected:
                    continue
                entities[kind].setdefault(identifier, item)
                provenance.add((kind, identifier, record.id))
                if (
                    kind in {"datasets", "dataset_series", "data_services"}
                    and item.get("uri") == record.root_uri
                ):
                    table = {
                        "datasets": "catalog_datasets",
                        "dataset_series": "catalog_series",
                        "data_services": "catalog_services",
                    }[kind]
                    links.update(
                        (table, catalog, identifier) for catalog in record.catalog_ids
                    )
            links.update(
                row
                for row in record.links
                if selected is None
                or (
                    not row[0].startswith("catalog_")
                    and (row[2] if row[0] == "series_datasets" else row[1]) in selected
                )
            )
        # External URI references stay in payloads, not invented rows or broken FKs.
        links = {
            row
            for row in links
            if row[1] in entities[LINKS[row[0]][0]]
            and row[2] in entities[LINKS[row[0]][1]]
        }
        return entities, sorted(links), sorted(provenance)
