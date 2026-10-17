"""Explicit convenient projections alongside lossless RDF term properties."""

import logging
import math
import re
from collections import defaultdict
from datetime import date, datetime

import rdflib
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, FOAF, RDF

from dataportalen_export.identity import stable_id
from dataportalen_export.models.catalog import Catalog
from dataportalen_export.models.data_service import DataService
from dataportalen_export.models.dataset import Dataset
from dataportalen_export.models.dataset_series import DatasetSeries
from dataportalen_export.models.distribution import Distribution
from dataportalen_export.models.publisher import Publisher
from dataportalen_export.rdf_graph import canonical_graph

rdflib.NORMALIZE_LITERALS = False
DCAT = Namespace("http://www.w3.org/ns/dcat#")
log = logging.getLogger(__name__)
MODELS = {
    "catalogs": Catalog,
    "datasets": Dataset,
    "dataset_series": DatasetSeries,
    "distributions": Distribution,
    "data_services": DataService,
    "publishers": Publisher,
}
TYPES = {
    DCAT.Catalog: "catalogs",
    DCAT.Dataset: "datasets",
    DCAT.DatasetSeries: "dataset_series",
    DCAT.Distribution: "distributions",
    DCAT.DataService: "data_services",
    FOAF.Agent: "publishers",
    FOAF.Organization: "publishers",
    FOAF.Person: "publishers",
}
LINKS = {
    "catalog_datasets": ("catalogs", "datasets"),
    "catalog_series": ("catalogs", "dataset_series"),
    "catalog_services": ("catalogs", "data_services"),
    "series_datasets": ("dataset_series", "datasets"),
    "dataset_distributions": ("datasets", "distributions"),
    "distribution_services": ("distributions", "data_services"),
    "service_datasets": ("data_services", "datasets"),
    "catalog_publishers": ("catalogs", "publishers"),
    "dataset_publishers": ("datasets", "publishers"),
    "series_publishers": ("dataset_series", "publishers"),
}


def parse_rdf(text: str, format: str = "turtle") -> Graph:
    try:
        return Graph().parse(data=text, format=format)
    except Exception as error:
        # RDFLib parsers raise several parser-specific exception families.
        raise ValueError(f"Invalid {format} RDF: {error}") from error


def closure(graph: Graph, root) -> Graph:
    """Walk owned descriptions without depth truncation or crossing datasets."""
    out, seen, pending = Graph(), set(), [root]
    stop_types = {DCAT.Dataset, DCAT.DatasetSeries, DCAT.Catalog}
    stop_predicates = {
        DCAT.dataset,
        DCAT.record,
        DCAT.catalog,
        DCAT.inSeries,
        DCAT.servesDataset,
        DCTERMS.hasPart,
        DCTERMS.isPartOf,
        FOAF.primaryTopic,
    }
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        for predicate, obj in graph.predicate_objects(node):
            out.add((node, predicate, obj))
            if isinstance(obj, Literal) or predicate in stop_predicates or obj in seen:
                continue
            if any(t in stop_types for t in graph.objects(obj, RDF.type)):
                continue
            if isinstance(obj, (BNode, URIRef)):
                pending.append(obj)
    return out


def language_map(graph, node, predicate):
    result = {}
    for value in graph.objects(node, predicate):
        if isinstance(value, Literal):
            result.setdefault(value.language or "und", []).append(str(value))
    return {key: sorted(set(values)) for key, values in sorted(result.items())}


def values(graph, node, predicate):
    return sorted({str(value) for value in graph.objects(node, predicate)})


def date_value(graph, node, predicate):
    return date_projection(values(graph, node, predicate), node)


def date_projection(candidates, node):
    """Project source date strings, including ISO reduced precision."""
    if not candidates:
        return None
    value = candidates[0].strip()
    try:
        if re.fullmatch(r"[0-9]{4}(?:-[0-9]{2})?", value):
            parts = [int(part) for part in value.split("-")]
            date(*(parts + [1] * (3 - len(parts))))
            # Validate without inventing a month/day that the source did not supply.
            return value
        if "T" in value or " " in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        log.warning("Retained invalid date %s on %s", value, node)
        return None


def byte_size_projection(candidates, node):
    if not candidates:
        return None
    value = candidates[0].strip()
    if re.fullmatch(r"[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+(?:\.[0-9]+)?", value):
        value = re.sub(r"\s", "", value)
    try:
        number = float(value)
        if math.isfinite(number) and number >= 0:
            return number
    except ValueError:
        pass
    log.warning("Retained invalid byte size %s on %s", candidates[0], node)
    return None


class RdfConverter:
    entity_id = staticmethod(stable_id)

    def convert(
        self, graph: Graph, *, source: str, scope: str, aliases=None, catalog_ids=None
    ):
        graph = canonical_graph(graph)
        aliases, catalog_ids = aliases or {}, catalog_ids or {}
        kinds = defaultdict(set)
        for node, rdf_type in graph.subject_objects(RDF.type):
            if rdf_type in TYPES:
                kinds[node].add(TYPES[rdf_type])
        for node in graph.objects(None, DCTERMS.publisher):
            if (node, None, None) in graph:
                kinds[node].add("publishers")
        for roles in kinds.values():
            if "dataset_series" in roles:
                roles.discard("datasets")
        typed_nodes = sorted(
            ((node, kind) for node, roles in kinds.items() for kind in roles),
            key=lambda item: (str(item[0]), item[1]),
        )

        def identity(node, kind):
            uri = aliases.get(str(node), str(node))
            if kind == "catalogs":
                return catalog_ids.get(str(node), stable_id(kind, f"{source}:{uri}"))
            return stable_id(
                kind, f"{scope}:_:{node}" if isinstance(node, BNode) else uri
            )

        def term(value):
            if isinstance(value, Literal):
                return {
                    "value": str(value),
                    "language": value.language,
                    "datatype": str(value.datatype) if value.datatype else None,
                }
            return {
                "id": f"_:{scope}:{value}" if isinstance(value, BNode) else str(value)
            }

        entities = []
        for node, kind in typed_nodes:
            props = {}
            for predicate, obj in graph.predicate_objects(node):
                props.setdefault(str(predicate), []).append(term(obj))
            for items in props.values():
                items.sort(key=lambda v: str(sorted(v.items())))
            fields = dict(
                id=identity(node, kind),
                kind=kind,
                uri=aliases.get(str(node), str(node))
                if isinstance(node, URIRef)
                else None,
                types=values(graph, node, RDF.type),
                title=language_map(graph, node, DCTERMS.title),
                description=language_map(graph, node, DCTERMS.description),
                issued=date_value(graph, node, DCTERMS.issued),
                modified=date_value(graph, node, DCTERMS.modified),
                identifiers=values(graph, node, DCTERMS.identifier),
                homepages=values(graph, node, FOAF.homepage),
                extra_properties=props,
            )
            if kind == "publishers":
                fields["title"] = (
                    language_map(graph, node, FOAF.name) or fields["title"]
                )
            if kind == "datasets":
                fields.update(
                    keywords=language_map(graph, node, DCAT.keyword),
                    themes=values(graph, node, DCAT.theme),
                    access_rights=values(graph, node, DCTERMS.accessRights),
                )
            if kind == "distributions":
                fields.update(
                    access_urls=values(graph, node, DCAT.accessURL),
                    download_urls=values(graph, node, DCAT.downloadURL),
                    media_types=values(graph, node, DCAT.mediaType),
                    formats=values(graph, node, DCTERMS.format),
                    licenses=values(graph, node, DCTERMS.license),
                    byte_size=byte_size_projection(
                        values(graph, node, DCAT.byteSize), node
                    ),
                )
            if kind == "data_services":
                fields.update(
                    endpoint_urls=values(graph, node, DCAT.endpointURL),
                    endpoint_descriptions=values(graph, node, DCAT.endpointDescription),
                )
            entities.append(MODELS[kind](**fields))
        links = set()
        for node, kind in typed_nodes:
            relationships = [
                (
                    DCTERMS.publisher,
                    {
                        "catalogs": "catalog_publishers",
                        "datasets": "dataset_publishers",
                        "dataset_series": "series_publishers",
                    }.get(kind),
                    "publishers",
                    False,
                )
            ]
            relationships += {
                "catalogs": [
                    (DCAT.dataset, "catalog_datasets", "datasets", False),
                    (DCAT.dataset, "catalog_series", "dataset_series", False),
                    (DCAT.service, "catalog_services", "data_services", False),
                ],
                "datasets": [
                    (
                        DCAT.distribution,
                        "dataset_distributions",
                        "distributions",
                        False,
                    ),
                    (DCAT.inSeries, "series_datasets", "dataset_series", True),
                ],
                "distributions": [
                    (
                        DCAT.accessService,
                        "distribution_services",
                        "data_services",
                        False,
                    )
                ],
                "data_services": [
                    (DCAT.servesDataset, "service_datasets", "datasets", False)
                ],
            }.get(kind, [])
            for predicate, table, target_kind, reverse in relationships:
                if table is None:
                    continue
                for obj in graph.objects(node, predicate):
                    if obj in kinds and target_kind not in kinds[obj]:
                        continue
                    left, right = identity(node, kind), identity(obj, target_kind)
                    links.add((table, right, left) if reverse else (table, left, right))
        return entities, sorted(links)
