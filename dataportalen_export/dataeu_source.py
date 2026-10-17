import asyncio
import logging
from urllib.parse import quote

import httpx
from rdflib import Namespace, URIRef
from rdflib.namespace import DCTERMS, RDF

from dataportalen_export.current_state import SourceBatch, make_record
from dataportalen_export.identity import stable_id
from dataportalen_export.portal_client import REPO
from dataportalen_export.rdf_converter import parse_rdf

DCAT = Namespace("http://www.w3.org/ns/dcat#")
log = logging.getLogger(__name__)


class DataEuSource:
    async def harvest(self, client, previous, settings, *, force=False):
        batch = SourceBatch("dataeu")
        try:
            catalogs = [
                catalog
                for catalog in await client.eu_catalogs(settings.countries)
                if catalog not in settings.exclude_eu_catalogs
            ]
            if not catalogs:
                raise ValueError("No additional EU catalogs discovered")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            batch.failures["dataeu"] = str(error)[:400]
            return batch
        for catalog in catalogs:
            scope = f"dataeu:{catalog}"
            catalog_id = stable_id("catalogs", scope)
            catalog_native = f"catalog:{catalog}"
            catalog_key = stable_id("source", f"dataeu:{catalog_native}")
            batch.seen.add(catalog_key)
            try:
                response = await client.get(
                    f"{REPO}/catalogues/{quote(catalog, safe='')}.ttl",
                    accept="text/turtle",
                )
                graph = parse_rdf(response.content.decode("utf-8"))
                nodes = sorted(graph.subjects(RDF.type, DCAT.Catalog), key=str)
                if not nodes:
                    raise ValueError("No catalog resource in EU metadata")
                batch.records[catalog_key] = make_record(
                    graph,
                    source="dataeu",
                    native_id=catalog_native,
                    scope=scope,
                    root_uri=str(nodes[0]),
                    stamp="",
                    catalog_id_overrides={str(nodes[0]): catalog_id},
                )
                old = previous.get(catalog_key)
                if old and old.digest == batch.records[catalog_key].digest:
                    batch.records[catalog_key].fetched_at = old.fetched_at
            except (httpx.HTTPError, ValueError) as error:
                batch.failures[catalog_key] = str(error)[:400]
            try:
                refs = await client.eu_listing(catalog)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                batch.failures[scope] = str(error)[:400]
                continue
            if not refs and any(
                record.scope == scope and not record.native_id.startswith("catalog:")
                for record in previous.values()
            ):
                batch.failures[scope] = (
                    "Unexpected empty catalog listing; retaining prior contents"
                )
                continue
            batch.complete_scopes.add(scope)
            batch.successful_scopes.add(scope)
            log.info("EU catalog %s: %d records", catalog, len(refs))

            async def one(native, stamp):
                native_key = f"{catalog}:{native}"
                key = stable_id("source", f"dataeu:{native_key}")
                batch.seen.add(key)
                old = previous.get(key)
                if old and stamp and old.stamp == stamp and not force:
                    batch.records[key] = old
                    batch.reused += 1
                    return
                try:
                    dataset_response = await client.get(
                        f"{REPO}/datasets/{quote(native, safe='')}.ttl",
                        accept="text/turtle",
                    )
                    graph = parse_rdf(dataset_response.content.decode("utf-8"))
                    nodes = list(graph.subjects(RDF.type, DCAT.Dataset)) + list(
                        graph.subjects(RDF.type, DCAT.DatasetSeries)
                    )
                    if not nodes:
                        raise ValueError("Dataset absent from repository graph")
                    expected = URIRef("http://data.europa.eu/88u/dataset/" + native)
                    root = expected if expected in nodes else sorted(nodes, key=str)[0]
                    missing = {
                        str(obj)
                        for obj in graph.objects(None, DCAT.distribution)
                        if (obj, None, None) not in graph
                    }
                    for uri in sorted(missing):
                        response = await client.get(
                            f"{REPO}/distributions/{quote(uri.rstrip('/').rsplit('/', 1)[-1], safe='')}.ttl",
                            accept="text/turtle",
                            optional=True,
                        )
                        if response is not None:
                            graph += parse_rdf(response.content.decode("utf-8"))
                    record_response = await client.get(
                        f"{REPO}/records/{quote(native, safe='')}.ttl",
                        accept="text/turtle",
                        optional=True,
                    )
                    original = None
                    if record_response is not None:
                        record_graph = parse_rdf(
                            record_response.content.decode("utf-8")
                        )
                        for record_node in record_graph.subjects(
                            RDF.type, DCAT.CatalogRecord
                        ):
                            value = record_graph.value(
                                record_node,
                                URIRef("https://piveau.eu/ns/voc#originalURIRef"),
                            )
                            if value is None:
                                value = record_graph.value(
                                    record_node, DCTERMS.identifier
                                )
                            if value and str(value).startswith(
                                ("http://", "https://", "urn:")
                            ):
                                original = str(value)
                                break
                        graph += record_graph
                    root_uri = original or str(root)
                    record = make_record(
                        graph,
                        source="dataeu",
                        native_id=native_key,
                        scope=scope,
                        root_uri=root_uri,
                        stamp=stamp,
                        aliases={str(root): root_uri},
                        catalog_ids=[catalog_id],
                    )
                    if old and old.digest == record.digest:
                        record.fetched_at = old.fetched_at
                    batch.records[key] = record
                    batch.fetched += 1
                except (httpx.HTTPError, ValueError) as error:
                    batch.failures[key] = str(error)[:400]

            items = list(refs.items())
            for start in range(0, len(items), client.settings.concurrency):
                await asyncio.gather(
                    *(
                        one(native, stamp)
                        for native, stamp in items[
                            start : start + client.settings.concurrency
                        ]
                    )
                )
                if start % 200 == 0:
                    log.info(
                        "EU %s processed %d/%d",
                        catalog,
                        min(start + client.settings.concurrency, len(items)),
                        len(items),
                    )
        # A successful nonempty catalog discovery can confirm vanished catalog scopes.
        current_scopes = {f"dataeu:{catalog}" for catalog in catalogs}
        batch.complete_scopes.update(
            record.scope
            for record in previous.values()
            if record.source == "dataeu" and record.scope not in current_scopes
        )
        return batch
