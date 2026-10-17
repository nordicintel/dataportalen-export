"""Resolve EntryScape contexts without collapsing multiple memberships."""

import asyncio
from collections import defaultdict

import httpx
from rdflib.namespace import DCAT

from dataportalen_export.current_state import make_record
from dataportalen_export.identity import stable_id
from dataportalen_export.portal_client import STORE
from dataportalen_export.rdf_converter import parse_rdf


class ContextIndex:
    def __init__(self):
        self.entries = defaultdict(set)
        self.catalogs = defaultdict(list)
        self.complete = False

    async def load(self, client, batch, previous):
        # Restore catalog identity when a metadata endpoint is temporarily unavailable.
        for record in previous.values():
            if record.source == "dataportal" and record.native_id.startswith(
                "catalog:"
            ):
                context = record.native_id.split(":", 2)[1]
                for entity in record.entities:
                    if entity["kind"] == "catalogs":
                        self.catalogs[context].append((entity["id"], set()))
        catalog_rows = await client.solr("rdfType:http\\://www.w3.org/ns/dcat#Catalog")
        fresh_catalogs = defaultdict(list)

        async def catalog(child):
            context, entry = str(child["contextId"]), str(child["entryId"])
            native = f"catalog:{context}:{entry}"
            record_id = stable_id("source", f"dataportal:{native}")
            batch.seen.add(record_id)
            try:
                response = await client.get(
                    f"{STORE}/{context}/metadata/{entry}",
                    params={"format": "text/turtle"},
                    accept="text/turtle",
                )
                graph = parse_rdf(response.content.decode("utf-8"))
                nodes = sorted(graph.subjects(None, DCAT.Catalog), key=str)
                if not nodes:
                    raise ValueError("Catalog resource missing")
                ids = {
                    str(node): stable_id("catalogs", f"dataportal:{context}:{node}")
                    for node in nodes
                }
                record = make_record(
                    graph,
                    source="dataportal",
                    native_id=native,
                    scope="dataportal",
                    root_uri=str(nodes[0]),
                    stamp="",
                    catalog_id_overrides=ids,
                )
                for entity in record.entities:
                    if entity["kind"] == "catalogs":
                        entity["context_ids"] = [context]
                old = previous.get(record_id)
                if old and old.digest == record.digest:
                    record.fetched_at = old.fetched_at
                batch.records[record_id] = record
                for node in nodes:
                    fresh_catalogs[context].append(
                        (
                            ids[str(node)],
                            {str(v) for v in graph.objects(node, DCAT.dataset)},
                        )
                    )
            except (httpx.HTTPError, ValueError) as error:
                batch.failures[record_id] = str(error)[:400]

        await asyncio.gather(*(catalog(row) for row in catalog_rows))
        self.catalogs.update(fresh_catalogs)
        for kind in ("Dataset", "DatasetSeries", "DataService"):
            rows = await client.solr(
                f"rdfType:http\\://www.w3.org/ns/dcat#{kind} AND public:true"
            )
            for child in rows:
                context = str(child.get("contextId", ""))
                for props in (child.get("info") or {}).values():
                    for value in props.get("http://entrystore.org/terms/resource", []):
                        self.entries[value["value"]].add(context)
        self.complete = True

    def membership(self, uri: str):
        contexts = set(self.entries.get(uri, ()))
        prefix = STORE + "/"
        if uri.startswith(prefix):
            parts = uri[len(prefix) :].split("/")
            if len(parts) == 3 and parts[1] == "resource":
                contexts.add(parts[0])
        result = set()
        for context in contexts:
            catalogs = self.catalogs.get(context, [])
            for identifier, members in catalogs:
                if len(catalogs) == 1 or uri in members:
                    result.add(identifier)
        # Explicit catalog membership is useful even when the registry omits a series.
        for catalogs in self.catalogs.values():
            for identifier, members in catalogs:
                if uri in members:
                    result.add(identifier)
        return sorted(result)
