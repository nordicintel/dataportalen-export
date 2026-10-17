import asyncio
from pathlib import Path

import httpx

from dataportalen_export.config import Config
from dataportalen_export.harvester import Harvester
from dataportalen_export.portal_client import PortalClient
from dataportalen_export.refresh import check_sources


def test_public_catalog_checks_select_changed_context_and_detect_deletion():
    config = Config()
    config.sources.dataeu = False
    changed = False
    deleted = False
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"ETag": '"same-dump"'})
        query = request.url.params["query"]
        counts = {"one": 2, "two": 2}
        if "modified:[" in query:
            counts = {"one": 1} if changed else {}
        elif deleted:
            counts.pop("two")
        return httpx.Response(
            200,
            json={
                "results": sum(counts.values()),
                "facetFields": [
                    {
                        "name": "context",
                        "valueCount": len(counts),
                        "values": [
                            {
                                "name": f"https://admin.dataportal.se/store/{key}",
                                "count": value,
                            }
                            for key, value in counts.items()
                        ],
                    }
                ],
            },
        )

    async def run():
        nonlocal changed, deleted
        async with PortalClient(
            config.harvest, transport=httpx.MockTransport(respond)
        ) as client:
            first = await check_sources(client, config, {})
            assert first.changed_contexts is None
            manifest = {"checkpoints": first.checkpoints}
            unchanged = await check_sources(client, config, manifest)
            assert unchanged.unchanged
            assert unchanged.changed_contexts == set()
            changed = True
            one = await check_sources(client, config, manifest)
            assert not one.unchanged
            assert one.changed_contexts == {"one"}
            changed = False
            deleted = True
            removal = await check_sources(client, config, manifest)
            assert removal.changed_contexts == {"two"}
            assert not removal.unchanged
        assert all(r.method == "HEAD" or r.url.params["limit"] == "1" for r in requests)

    asyncio.run(run())


def test_unchanged_harvest_does_not_open_state_or_products(tmp_path, monkeypatch):
    from rdflib import Graph

    config = Config()
    config.sources.dataeu = False
    config.output.directory = tmp_path / "data"
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    dump = graph.serialize(format="xml").encode()
    metadata_requests = []

    def respond(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={"ETag": '"same"'})
        if request.url.path == "/all.rdf":
            return httpx.Response(200, content=dump, headers={"ETag": '"same"'})
        if "/metadata/" in request.url.path:
            metadata_requests.append(request.url.path)
            return httpx.Response(
                200, content=graph.serialize(format="turtle").encode()
            )
        query = request.url.params["query"]
        if "facetFields" in request.url.params:
            counts = (
                []
                if "modified:[" in query
                else [{"name": "https://admin.dataportal.se/store/one", "count": 3}]
            )
            return httpx.Response(
                200,
                json={
                    "results": 3 if counts else 0,
                    "facetFields": [
                        {"name": "context", "valueCount": len(counts), "values": counts}
                    ],
                },
            )
        if "#Catalog" in query:
            rows = [{"contextId": "one", "entryId": "catalog"}]
        else:
            rows = (
                [
                    {
                        "contextId": "one",
                        "entryId": "dataset",
                        "info": {
                            "entry": {
                                "http://entrystore.org/terms/resource": [
                                    {"value": "https://example.org/Dataset"}
                                ]
                            }
                        },
                    }
                ]
                if "#Dataset" in query and "#DatasetSeries" not in query
                else []
            )
        return httpx.Response(
            200, json={"results": len(rows), "resource": {"children": rows}}
        )

    transport = httpx.MockTransport(respond)
    first = asyncio.run(Harvester(transport=transport).run(config))
    products = {
        p: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in config.output.directory.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path not in products, f"Unchanged run opened published product {path}"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    second = asyncio.run(Harvester(transport=transport).run(config))
    assert second["no_changes"]
    assert second["files"] == first["files"]
    assert len(metadata_requests) == 1
    assert all(
        (p.stat().st_mtime_ns, p.stat().st_size) == stats
        for p, stats in products.items()
    )
