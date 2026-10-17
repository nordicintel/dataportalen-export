"""Bounded HTTP requests and completeness-checked portal listings."""

import asyncio
import json
import logging
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from dataportalen_export.config import Harvest

SEARCH = "https://data.europa.eu/api/hub/search"
REPO = "https://data.europa.eu/api/hub/repo"
STORE = "https://admin.dataportal.se/store"
DUMP = "https://admin.dataportal.se/all.rdf"
log = logging.getLogger(__name__)


class PortalClient:
    def __init__(self, settings: Harvest, *, transport=None):
        self.settings = settings
        self.semaphore = asyncio.Semaphore(settings.concurrency)
        self.client = httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": "dataportalen-export/1.0 (+https://github.com/nordicintel/dataportalen-export)"
            },
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def get(
        self,
        url: str,
        *,
        params=None,
        accept="application/json",
        optional=False,
        method="GET",
    ):
        for attempt in range(self.settings.max_attempts):
            response = None
            try:
                async with self.semaphore:
                    response = await self.client.request(
                        method, url, params=params, headers={"Accept": accept}
                    )
                if optional and response.status_code == 404:
                    return None
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError(
                        f"Incomplete HTTP response {response.status_code}: {url}"
                    )
                if accept == "application/json":
                    response.json()
                elif accept == "text/turtle":
                    body = response.content.lstrip().lower()
                    if body.startswith((b"<!doctype html", b"<html")) or not body:
                        raise ValueError(f"Invalid RDF response: {url}")
                return response
            except (httpx.HTTPError, ValueError) as error:
                if (
                    isinstance(error, httpx.HTTPStatusError)
                    and response.status_code < 500
                    and response.status_code not in (408, 429)
                ):
                    raise
                if attempt + 1 == self.settings.max_attempts:
                    raise
                delay = min(2**attempt + random.random(), 30)
                if response is not None and "Retry-After" in response.headers:
                    value = response.headers["Retry-After"]
                    try:
                        delay = float(value)
                    except ValueError:
                        try:
                            delay = (
                                parsedate_to_datetime(value) - datetime.now(UTC)
                            ).total_seconds()
                        except (TypeError, ValueError):
                            pass
                log.debug("Retry %s: %s", url, error)
                await asyncio.sleep(max(0, min(delay, 60)))
        raise RuntimeError("Unreachable retry state")

    async def download(self, url: str, target: Path, *, validators=None):
        target.parent.mkdir(parents=True, exist_ok=True)
        validators = validators or {}
        headers = {
            header: validators[key]
            for key, header in (
                ("etag", "If-None-Match"),
                ("last-modified", "If-Modified-Since"),
            )
            if validators.get(key)
        }
        for attempt in range(self.settings.max_attempts):
            try:
                async with self.semaphore:
                    async with self.client.stream(
                        "GET",
                        url,
                        headers=headers,
                        timeout=self.settings.download_timeout_seconds,
                    ) as response:
                        if response.status_code == 304 and headers:
                            return None
                        response.raise_for_status()
                        if response.status_code != 200:
                            raise ValueError("Partial dump response")
                        with target.open("wb") as stream:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                stream.write(chunk)
                        if not target.stat().st_size:
                            raise ValueError("Empty dump response")
                        return dict(response.headers)
            except (httpx.HTTPError, ValueError):
                if attempt + 1 == self.settings.max_attempts:
                    raise
                await asyncio.sleep(min(2**attempt, 30))

    async def solr(self, query: str):
        async def page(offset):
            response = await self.get(
                f"{STORE}/search",
                params={"type": "solr", "query": query, "limit": 100, "offset": offset},
            )
            return response.json()

        first = await page(0)
        total = int(first.get("results", 0))
        rows = list((first.get("resource") or {}).get("children") or [])
        for start in range(100, total, 100 * self.settings.concurrency):
            pages = await asyncio.gather(
                *(
                    page(offset)
                    for offset in range(
                        start, min(total, start + 100 * self.settings.concurrency), 100
                    )
                )
            )
            for result in pages:
                rows.extend((result.get("resource") or {}).get("children") or [])
        if len(rows) != total:
            raise ValueError(f"Incomplete EntryScape listing: {len(rows)}/{total}")
        return rows

    async def context_counts(self, *, since=None):
        """Small grouped change check through Dataportal's documented catalog API."""
        kinds = " OR ".join(
            f"rdfType:http\\://www.w3.org/ns/dcat#{kind}"
            for kind in ("Catalog", "Dataset", "DatasetSeries", "DataService")
        )
        query = f"public:true AND ({kinds})"
        if since:
            query += f" AND modified:[{since} TO *]"
        result = (
            await self.get(
                f"{STORE}/search",
                params={
                    "type": "solr",
                    "query": query,
                    "limit": 1,
                    "facetFields": "context",
                    "facetLimit": 1000,
                },
            )
        ).json()
        facet = next(f for f in result["facetFields"] if f["name"] == "context")
        counts = {
            v["name"].rstrip("/").rsplit("/", 1)[-1]: int(v["count"])
            for v in facet["values"]
        }
        if len(counts) != int(facet["valueCount"]) or sum(counts.values()) != int(
            result["results"]
        ):
            raise ValueError("Incomplete Dataportal context counts")
        return counts

    async def eu_catalogs(self, countries):
        response = await self.get(
            f"{SEARCH}/search",
            params={
                "filter": "catalogue",
                "limit": 200,
                "facets": json.dumps({"country": countries}),
                "includes": "id,modified",
            },
        )
        rows = response.json()["result"]["results"]
        if len(rows) >= 200:
            raise ValueError("Catalog listing may be truncated")
        return {str(row["id"]): str(row.get("modified") or "") for row in rows}

    async def eu_listing(self, catalog: str):
        response = await self.get(
            f"{SEARCH}/search",
            params={
                "filter": "dataset",
                "limit": 1000,
                "facets": json.dumps({"catalog": [catalog]}),
                "includes": "id,catalog_record.modified",
                "scroll": "true",
            },
        )
        result = response.json()["result"]
        expected, rows = int(result.get("count", 0)), {}
        while True:
            page = result.get("results") or []
            if page and not {str(item["id"]) for item in page} - rows.keys():
                raise ValueError("Repeated search page")
            for item in page:
                rows[str(item["id"])] = str(
                    (item.get("catalog_record") or {}).get("modified") or ""
                )
            scroll = result.get("scrollId")
            if not page or not scroll:
                break
            result = (
                await self.get(f"{SEARCH}/scroll", params={"scrollId": scroll})
            ).json().get("result") or {}
        if len(rows) != expected:
            raise ValueError(f"Incomplete search listing: {len(rows)}/{expected}")
        repository_ids, offset = set(), 0
        while True:
            page = (
                await self.get(
                    f"{REPO}/catalogues/{quote(catalog, safe='')}/datasets",
                    params={
                        "limit": 5000,
                        "offset": offset,
                        "valueType": "identifiers",
                    },
                )
            ).json()
            if not isinstance(page, list):
                raise ValueError("Invalid repository listing")
            new = {str(item) for item in page}
            if page and not new - repository_ids:
                raise ValueError("Repeated repository page")
            repository_ids.update(new)
            if len(page) < 5000:
                break
            offset += 5000
        # Keep the union: index/repository disagreements are not deletion evidence.
        return {key: rows.get(key, "") for key in sorted(repository_ids | rows.keys())}
