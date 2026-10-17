import json
from collections import defaultdict

from rdflib import BNode, Graph

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
    def write(self, directory, state, entities, links, provenance):
        directory.mkdir(parents=True, exist_ok=True)
        memberships = defaultdict(set)
        for table, left, right in links:
            if table in {"catalog_datasets", "catalog_series", "catalog_services"}:
                memberships[right].add(left)
        by_catalog = defaultdict(set)
        for kind, identifier, record in provenance:
            if kind == "catalogs":
                by_catalog[identifier].add(record)
            for catalog in memberships.get(identifier, ()):
                by_catalog[catalog].add(record)
        # Source-qualified named graphs retain conflicting variants independently.
        for catalog in sorted(entities["catalogs"]):
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
