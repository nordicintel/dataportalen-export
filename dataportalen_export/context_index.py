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
        self.resource_ids = defaultdict(set)
        self.retained_memberships = defaultdict(set)
        self.complete = False

    async def load(
        self, client, batch, previous, *, force=False, changed_contexts=None
    ):
        # Restore catalog identity when a metadata endpoint is temporarily unavailable.
        for record in previous.values():
            if record.source == "dataportal" and record.native_id.startswith(
                "catalog:"
            ):
                context = record.native_id.split(":", 2)[1]
                for entity in record.entities:
                    if entity["kind"] == "catalogs":
                        self.resource_ids[entity["uri"]].add(entity["id"])
                        members = {
                            value["id"]
                            for value in entity["extra_properties"].get(
                                str(DCAT.dataset), []
                            )
                            if "id" in value
                        }
                        self.catalogs[context].append((entity["id"], members))
        owners = {
            identifier: context
            for context, rows in self.catalogs.items()
            for identifier, _ in rows
        }
        if changed_contexts is not None:
            for record in previous.values():
                if record.source != "dataportal":
                    continue
                if record.native_id.startswith("catalog:"):
                    if record.native_id.split(":", 2)[1] not in changed_contexts:
                        batch.records[record.id] = record
                        batch.seen.add(record.id)
                        batch.reused += 1
                else:
                    self.retained_memberships[record.root_uri].update(
                        identifier
                        for identifier in record.catalog_ids
                        if owners.get(identifier) not in changed_contexts
                    )

        async def rows_for(kind):
            query = f"rdfType:http\\://www.w3.org/ns/dcat#{kind} AND public:true"
            if changed_contexts is None:
                return await client.solr(query)
            contexts = sorted(changed_contexts)
            rows = []
            for start in range(0, len(contexts), 30):
                selected = " OR ".join(
                    f'context:"{STORE}/{context}"'
                    for context in contexts[start : start + 30]
                )
                rows.extend(await client.solr(f"{query} AND ({selected})"))
            return rows

        catalog_rows = await rows_for("Catalog")
        fresh_catalogs = defaultdict(list)
        failed_contexts = set()

        async def catalog(child):
            context, entry = str(child["contextId"]), str(child["entryId"])
            native = f"catalog:{context}:{entry}"
            record_id = stable_id("source", f"dataportal:{native}")
            batch.seen.add(record_id)
            old = previous.get(record_id)
            stamp = next(
                (
                    value["value"]
                    for properties in (child.get("info") or {}).values()
                    for value in properties.get("http://purl.org/dc/terms/modified", [])
                    if value.get("value")
                ),
                "",
            )
            if old and stamp and old.stamp == stamp and not force:
                batch.records[record_id] = old
                batch.reused += 1
                old_ids = {e["id"] for e in old.entities if e["kind"] == "catalogs"}
                fresh_catalogs[context].extend(
                    row for row in self.catalogs[context] if row[0] in old_ids
                )
                return
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
                for uri, identifier in ids.items():
                    self.resource_ids[uri].add(identifier)
                record = make_record(
                    graph,
                    source="dataportal",
                    native_id=native,
                    scope="dataportal",
                    root_uri=str(nodes[0]),
                    stamp=stamp,
                    catalog_id_overrides=ids,
                )
                for entity in record.entities:
                    if entity["kind"] == "catalogs":
                        entity["context_ids"] = [context]
                if old and old.digest == record.digest:
                    record.fetched_at = old.fetched_at
                    record.rdf = old.rdf
                batch.records[record_id] = record
                batch.fetched += 1
                for node in nodes:
                    fresh_catalogs[context].append(
                        (
                            ids[str(node)],
                            {str(v) for v in graph.objects(node, DCAT.dataset)},
                        )
                    )
            except (httpx.HTTPError, ValueError) as error:
                failed_contexts.add(context)
                batch.failures[record_id] = str(error)[:400]

        await asyncio.gather(*(catalog(row) for row in catalog_rows))
        for context in failed_contexts:
            fresh_ids = {identifier for identifier, _ in fresh_catalogs[context]}
            fresh_catalogs[context].extend(
                row for row in self.catalogs[context] if row[0] not in fresh_ids
            )
        for context in list(self.catalogs):
            if changed_contexts is None or context in changed_contexts:
                del self.catalogs[context]
        self.catalogs.update(fresh_catalogs)
        active_ids = {
            identifier for rows in self.catalogs.values() for identifier, _ in rows
        }
        self.resource_ids = defaultdict(
            set,
            {
                uri: ids & active_ids
                for uri, ids in self.resource_ids.items()
                if ids & active_ids
            },
        )
        for kind in ("Dataset", "DatasetSeries", "DataService"):
            rows = await rows_for(kind)
            for child in rows:
                context = str(child.get("contextId", ""))
                for props in (child.get("info") or {}).values():
                    for value in props.get("http://entrystore.org/terms/resource", []):
                        self.entries[value["value"]].add(context)
        self.complete = not failed_contexts

    def membership(self, uri: str):
        contexts = set(self.entries.get(uri, ()))
        prefix = STORE + "/"
        if uri.startswith(prefix):
            parts = uri[len(prefix) :].split("/")
            if len(parts) == 3 and parts[1] == "resource":
                contexts.add(parts[0])
        result = set(self.retained_memberships.get(uri, ()))
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
