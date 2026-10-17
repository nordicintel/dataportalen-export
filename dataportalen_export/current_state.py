"""One retained version per source record; failed scopes never imply deletion."""

import gzip
import lzma
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
    byte_size_projection,
    date_projection,
)
from dataportalen_export.rdf_graph import graph_digest

SCHEMA_VERSION = 2


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
    def __init__(self, records=None):
        self.records: dict[str, SourceRecord] = records or {}
        self.deleted = 0

    @classmethod
    def load(cls, directory: Path):
        path = next(
            (
                directory / name
                for name in ("source_records.jsonl.xz", "source_records.jsonl.gz")
                if (directory / name).exists()
            ),
            None,
        )
        if path is None:
            return cls()
        opener = lzma.open if path.suffix == ".xz" else gzip.open
        with opener(path, "rt", encoding="utf-8") as stream:
            records = [
                SourceRecord.model_validate_json(line)
                for line in stream
                if line.strip()
            ]
        # Repair typed projections from retained lexical values without refetching
        # unchanged RDF or assigning a new successful-fetch timestamp.
        for record in records:
            for entity in record.entities:
                for name in ("issued", "modified"):
                    values = entity["extra_properties"].get(
                        f"http://purl.org/dc/terms/{name}", []
                    )
                    entity[name] = date_projection(
                        sorted(
                            {item.get("value", item.get("id", "")) for item in values}
                        ),
                        entity.get("uri") or entity["id"],
                    )
                if entity["kind"] == "distributions":
                    sizes = entity["extra_properties"].get(
                        "http://www.w3.org/ns/dcat#byteSize", []
                    )
                    entity["byte_size"] = byte_size_projection(
                        sorted(
                            {item.get("value", item.get("id", "")) for item in sizes}
                        ),
                        entity.get("uri") or entity["id"],
                    )
        return cls({r.id: r for r in records})

    def apply(self, batch: SourceBatch):
        for key, old in list(self.records.items()):
            if old.source != batch.source:
                continue
            if key in batch.records:
                continue
            if old.scope in batch.complete_scopes and key not in batch.seen:
                del self.records[key]
                self.deleted += 1
            else:
                self.records[key] = old.model_copy(update={"stale": True})
        for key, record in batch.records.items():
            self.records[key] = record.model_copy(update={"stale": False})

    def materialize(self):
        entities = {kind: {} for kind in MODELS}
        links, provenance = set(), set()
        records = sorted(
            self.records.values(), key=lambda r: (r.source != "dataportal", r.id)
        )
        for record in records:
            for item in record.entities:
                kind, identifier = item["kind"], item["id"]
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
            links.update(record.links)
        # External URI references stay in payloads, not invented rows or broken FKs.
        links = {
            row
            for row in links
            if row[1] in entities[LINKS[row[0]][0]]
            and row[2] in entities[LINKS[row[0]][1]]
        }
        return entities, sorted(links), sorted(provenance)
