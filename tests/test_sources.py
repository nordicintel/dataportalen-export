import asyncio
from pathlib import Path

import httpx
import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, FOAF

from dataportalen_export.config import Harvest, Sources
from dataportalen_export.current_state import CurrentState
from dataportalen_export.dataeu_source import DataEuSource
from dataportalen_export.dataportal_source import DataportalSource
from dataportalen_export.portal_client import PortalClient


def test_eu_listing_reconciles_repository_only_records_and_retries():
    calls = 0

    def respond(request):
        nonlocal calls
        if request.url.path.endswith("/search"):
            calls += 1
            if calls == 1:
                return httpx.Response(503, headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                json={
                    "result": {
                        "count": 1,
                        "results": [
                            {"id": "a", "catalog_record": {"modified": "today"}}
                        ],
                    }
                },
            )
        return httpx.Response(200, json=["a", "b"])

    async def run():
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            refs = await client.eu_listing("test")
            assert refs == {"a": "today", "b": ""}

    asyncio.run(run())
    assert calls == 2


def test_failed_catalog_metadata_preserves_known_memberships(tmp_path):
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    graph.parse(
        data="<https://example.org/catalog> a <http://www.w3.org/ns/dcat#Dataset> .",
        format="turtle",
    )
    dump = graph.serialize(format="xml").encode()
    catalog = """@prefix dcat: <http://www.w3.org/ns/dcat#> .
        <https://example.org/catalog> a dcat:Catalog;
            dcat:dataset <https://example.org/Dataset>, <https://example.org/series> .
        <https://example.org/another> a dcat:Catalog; <https://example.org/detail> _:a .
        _:a <https://example.org/next> _:b . _:b <https://example.org/next> _:a ."""
    fail_metadata = False

    def respond(request):
        if request.url.path == "/all.rdf":
            return httpx.Response(200, content=dump)
        if "/metadata/" in request.url.path:
            return httpx.Response(404 if fail_metadata else 200, text=catalog)
        query = request.url.params["query"]
        if "#Catalog" in query:
            rows = [{"contextId": "context", "entryId": "catalog"}]
            # EntryScape's unrestricted index count includes inaccessible entries.
            if "public:true" not in query:
                return httpx.Response(
                    200, json={"results": 2, "resource": {"children": rows}}
                )
        else:
            uri = "series" if "#DatasetSeries" in query else "Dataset"
            rows = [
                {
                    "contextId": "context",
                    "entryId": uri,
                    "info": {
                        "entry": {
                            "http://entrystore.org/terms/resource": [
                                {"value": "https://example.org/" + uri}
                            ]
                        }
                    },
                }
            ]
        return httpx.Response(
            200, json={"results": len(rows), "resource": {"children": rows}}
        )

    async def run():
        nonlocal fail_metadata
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            first, _ = await DataportalSource().harvest(client, {}, tmp_path)
            state = CurrentState(first.records)
            assert len(state.materialize()[0]["catalogs"]) == 2
            unchanged, _ = await DataportalSource().harvest(
                client, state.records, tmp_path
            )
            assert {key: record.rdf for key, record in unchanged.records.items()} == {
                key: record.rdf for key, record in first.records.items()
            }
            before = {
                r.root_uri: r.catalog_ids
                for r in first.records.values()
                if r.catalog_ids
            }
            assert "https://example.org/Dataset" in before
            assert "https://example.org/series" in before
            fail_metadata = True
            second, _ = await DataportalSource().harvest(
                client, state.records, tmp_path
            )
            state.apply(second)
            assert not second.complete_scopes
            assert second.failures
            after = {
                r.root_uri: r.catalog_ids
                for r in state.records.values()
                if r.catalog_ids
            }
            assert after == before

    asyncio.run(run())


def test_truncated_listing_does_not_look_complete():
    def respond(request):
        return httpx.Response(
            200, json={"result": {"count": 2, "results": [{"id": "a"}]}}
        )

    async def run():
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            with pytest.raises(ValueError, match="Incomplete"):
                await client.eu_listing("test")

    asyncio.run(run())


def test_scroll_cursor_can_remain_the_same_between_pages():
    pages = iter([[{"id": "b"}], []])

    def respond(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "result": {"count": 2, "results": [{"id": "a"}], "scrollId": "same"}
                },
            )
        if request.url.path.endswith("/scroll"):
            return httpx.Response(
                200, json={"result": {"results": next(pages), "scrollId": "same"}}
            )
        return httpx.Response(200, json=["a", "b"])

    async def run():
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            assert await client.eu_listing("test") == {"a": "", "b": ""}

    asyncio.run(run())


def test_dataportal_skips_unchanged_dump_and_catalog_downloads(tmp_path):
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    dump = graph.serialize(format="xml").encode()
    version = "one"
    downloaded = []

    def respond(request):
        if request.url.path == "/all.rdf":
            if request.headers.get("If-None-Match") == '"dump-one"':
                return httpx.Response(304)
            downloaded.append("dump")
            return httpx.Response(200, content=dump, headers={"ETag": '"dump-one"'})
        if "/metadata/" in request.url.path:
            downloaded.append("catalog")
            return httpx.Response(
                200,
                text="""
                @prefix dcat: <http://www.w3.org/ns/dcat#> .
                <https://example.org/catalog> a dcat:Catalog;
                    dcat:dataset <https://example.org/Dataset>, <https://example.org/series> .
            """,
            )
        catalog = "#Catalog" in request.url.params["query"]
        rows = (
            [
                {
                    "contextId": "one",
                    "entryId": "catalog",
                    "info": {
                        "entry": {
                            "http://purl.org/dc/terms/modified": [{"value": version}],
                        }
                    },
                }
            ]
            if catalog
            else []
        )
        return httpx.Response(
            200, json={"results": len(rows), "resource": {"children": rows}}
        )

    async def run():
        nonlocal version
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            first, raw = await DataportalSource().harvest(
                client, {}, tmp_path / "first"
            )
            status = {
                "http": first.http,
                "complete_scopes": ["dataportal"],
                "failure_count": 0,
            }
            second, _ = await DataportalSource().harvest(
                client,
                first.records,
                tmp_path / "second",
                cached_dump=raw,
                previous_status=status,
            )
            assert downloaded == ["dump", "catalog"]
            assert second.records == first.records
            assert second.reused == len(first.records)
            version = "two"
            changed, _ = await DataportalSource().harvest(
                client,
                second.records,
                tmp_path / "third",
                cached_dump=raw,
                previous_status=status,
            )
            assert downloaded == ["dump", "catalog", "catalog"]
            assert (
                next(
                    r
                    for r in changed.records.values()
                    if r.native_id.startswith("catalog:")
                ).stamp
                == "two"
            )
            await DataportalSource().harvest(
                client,
                changed.records,
                tmp_path / "forced",
                cached_dump=raw,
                previous_status=status,
                force=True,
            )
            assert downloaded[-2:] == ["dump", "catalog"]

    asyncio.run(run())


def test_eu_skips_unchanged_catalog_and_dataset_metadata():
    stamp = "2026-10-17T00:00:00Z"
    downloaded = []

    def respond(request):
        path = request.url.path
        if path.endswith("/search"):
            catalog = request.url.params["filter"] == "catalogue"
            row = (
                {"id": "test", "modified": stamp}
                if catalog
                else {"id": "dataset", "catalog_record": {"modified": stamp}}
            )
            return httpx.Response(200, json={"result": {"count": 1, "results": [row]}})
        if path.endswith("/datasets"):
            return httpx.Response(200, json=["dataset"])
        downloaded.append(path)
        if "/catalogues/" in path:
            return httpx.Response(
                200,
                text=f'<urn:catalog> a <http://www.w3.org/ns/dcat#Catalog>; <http://purl.org/dc/terms/modified> "{stamp}" .',
            )
        if "/datasets/" in path:
            return httpx.Response(
                200,
                text="<urn:dataset> a <http://www.w3.org/ns/dcat#Dataset>; <http://www.w3.org/ns/dcat#distribution> <urn:distribution> .",
            )
        if "/distributions/" in path:
            return httpx.Response(
                200,
                text="<urn:distribution> a <http://www.w3.org/ns/dcat#Distribution> .",
            )
        return httpx.Response(404)

    async def run():
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            first = await DataEuSource().harvest(client, {}, Sources())
            assert not first.failures
            assert (
                len(CurrentState(first.records).materialize()[0]["distributions"]) == 1
            )
            count = len(downloaded)
            # Existing exports already retain the catalog's own modification value.
            next(
                r for r in first.records.values() if r.native_id.startswith("catalog:")
            ).stamp = ""
            second = await DataEuSource().harvest(client, first.records, Sources())
            assert len(downloaded) == count
            assert second.reused == 2
            assert second.records == first.records

    asyncio.run(run())


def test_one_changed_context_does_not_enumerate_other_catalogs(tmp_path, monkeypatch):
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    graph += Graph().parse(
        data=graph.serialize(format="turtle").replace(
            "https://example.org/", "https://other.example.org/"
        ),
        format="turtle",
    )
    graph.set(
        (
            URIRef("https://other.example.org/Dataset"),
            DCTERMS.publisher,
            URIRef("https://example.org/publisher"),
        )
    )
    stamp = "one"
    dump_stamp = "one"
    queries, metadata = [], []
    scoped = False
    removed = False

    def respond(request):
        if request.url.path == "/all.rdf":
            if request.headers.get("If-None-Match") == f'"{dump_stamp}"':
                return httpx.Response(304)
            return httpx.Response(
                200,
                content=graph.serialize(format="xml").encode(),
                headers={"ETag": f'"{dump_stamp}"'},
            )
        if "/metadata/" in request.url.path:
            context = request.url.path.split("/")[2]
            metadata.append(context)
            prefix = (
                "https://example.org/"
                if context == "one"
                else "https://other.example.org/"
            )
            return httpx.Response(
                200, text=f"<{prefix}catalog> a <http://www.w3.org/ns/dcat#Catalog> ."
            )
        query = request.url.params["query"]
        queries.append(query)
        if scoped:
            assert "context:" in query and "/one" in query and "/two" not in query
        rows = []
        for context, prefix in (
            ("one", "https://example.org/"),
            ("two", "https://other.example.org/"),
        ):
            if scoped and (context != "one" or removed):
                continue
            kind = "catalog" if "#Catalog" in query else "Dataset"
            if "#DatasetSeries" in query or "#DataService" in query:
                continue
            rows.append(
                {
                    "contextId": context,
                    "entryId": kind,
                    "info": {
                        "entry": {
                            "http://purl.org/dc/terms/modified": [{"value": stamp}],
                            "http://entrystore.org/terms/resource": [
                                {"value": prefix + kind}
                            ],
                        }
                    },
                }
            )
        return httpx.Response(
            200, json={"results": len(rows), "resource": {"children": rows}}
        )

    async def run():
        nonlocal stamp, scoped, removed, dump_stamp
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            first, raw = await DataportalSource().harvest(
                client, {}, tmp_path / "first"
            )
            status = {
                "http": first.http,
                "complete_scopes": ["dataportal"],
                "failure_count": 0,
            }
            old_other = next(
                r
                for r in first.records.values()
                if r.root_uri == "https://other.example.org/Dataset"
            )
            assert old_other.catalog_ids
            stamp, scoped = "two", True
            dump_stamp = "two"
            graph.set(
                (
                    URIRef("https://example.org/Dataset"),
                    DCTERMS.title,
                    Literal("Changed"),
                )
            )
            queries.clear()
            metadata.clear()
            original_walk = Graph.predicate_objects

            def guarded_walk(self, subject=None, *args, **kwargs):
                if self is not graph:
                    assert subject != URIRef("https://other.example.org/Dataset"), (
                        "Traversed an unchanged catalog's RDF"
                    )
                return original_walk(self, subject, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(Graph, "predicate_objects", guarded_walk)
                second, _ = await DataportalSource().harvest(
                    client,
                    first.records,
                    tmp_path / "second",
                    cached_dump=raw,
                    previous_status=status,
                    changed_contexts={"one"},
                )
            assert not second.failures
            assert second.records[old_other.id] == old_other
            assert metadata == ["one"]
            assert len(queries) == 4
            dump_stamp, stamp = "three", "three"
            graph.set(
                (
                    URIRef("https://example.org/publisher"),
                    FOAF.name,
                    Literal("Changed publisher"),
                )
            )
            shared, _ = await DataportalSource().harvest(
                client,
                second.records,
                tmp_path / "shared",
                cached_dump=raw,
                previous_status=status,
                changed_contexts={"one"},
            )
            # The shared publisher invalidates the dependent dataset across contexts.
            assert shared.records[old_other.id].digest != old_other.digest
            assert any(
                e["title"] == {"und": ["Changed publisher"]}
                for e in shared.records[old_other.id].entities
                if e["kind"] == "publishers"
            )
            removed = True
            third, _ = await DataportalSource().harvest(
                client,
                shared.records,
                tmp_path / "third",
                cached_dump=raw,
                previous_status=status,
                changed_contexts={"one"},
            )
            own = next(
                r
                for r in third.records.values()
                if r.root_uri == "https://example.org/Dataset"
            )
            assert not own.catalog_ids
            assert third.records[old_other.id].catalog_ids == old_other.catalog_ids
            assert not any(
                r.native_id.startswith("catalog:one:") for r in third.records.values()
            )

    asyncio.run(run())
