import json
from collections import defaultdict

from rdflib import BNode, Graph

from dataportalen_export.exports.jsonl_exporter import rows_digest
from dataportalen_export.rdf_converter import parse_rdf
from dataportalen_export.rdf_graph import canonical_graph
from dataportalen_export.schema import json_text

CONTEXT = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


class JsonldExporter:
    def write(self, directory, state, entities, links, provenance, *, reuse=None):
        directory.mkdir(parents=True, exist_ok=True)
        memberships = defaultdict(set)
        for table, left, right in links:
            if table in {"catalog_datasets", "catalog_series", "catalog_services"}:
                memberships[right].add(left)
        # Include source variants that supply referenced descriptions separately
        # from a dataset's own RDF (including a retained last-known publisher).
        dependencies = defaultdict(
            set, {key: set(value) for key, value in memberships.items()}
        )
        changed = bool(state.referenced_records)
        while changed:
            changed = False
            for table, left, right in links:
                if table.startswith("catalog_"):
                    continue
                owner, target = (
                    (right, left) if table == "series_datasets" else (left, right)
                )
                before = len(dependencies[target])
                dependencies[target].update(dependencies[owner])
                changed |= len(dependencies[target]) != before
        by_catalog = defaultdict(set)
        for kind, identifier, record in provenance:
            if kind == "catalogs":
                by_catalog[identifier].add(record)
            owner_catalogs = (
                dependencies if record in state.referenced_records else memberships
            )
            for catalog in owner_catalogs.get(identifier, ()):
                by_catalog[catalog].add(record)
        # Source-qualified named graphs retain conflicting variants independently.
        for catalog in sorted(entities["catalogs"]):
            if reuse and reuse(
                f"jsonld/{catalog}.jsonld",
                rows_digest(
                    (record_id, state.records[record_id].digest)
                    for record_id in sorted(by_catalog[catalog])
                ),
            ):
                continue
            graphs = []
            for record_id in sorted(by_catalog[catalog]):
                canonical = canonical_graph(parse_rdf(state.records[record_id].rdf))
                graph = Graph()
                for triple in canonical:
                    graph.add(
                        tuple(
                            BNode(f"{record_id}_{term}")
                            if isinstance(term, BNode)
                            else term
                            for term in triple
                        )
                    )
                body = json.loads(
                    graph.serialize(
                        format="json-ld", context=CONTEXT, auto_compact=True
                    )
                )
                body.pop("@context", None)
                nodes = stable_jsonld(body.get("@graph", [body]))
                graphs.append({"@id": f"urn:source:{record_id}", "@graph": nodes})
            (directory / f"{catalog}.jsonld").write_text(
                json_text({"@context": CONTEXT, "@graph": graphs}) + "\n",
                encoding="utf-8",
            )


def stable_jsonld(value, *, ordered=False):
    if isinstance(value, dict):
        return {
            key: stable_jsonld(item, ordered=key == "@list")
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [stable_jsonld(item) for item in value]
        return items if ordered else sorted(items, key=json_text)
    return value
