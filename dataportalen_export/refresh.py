"""Select source work using public catalog modification signals, before state loading."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from dataportalen_export.current_state import SCHEMA_VERSION, now_iso
from dataportalen_export.portal_client import DUMP
from dataportalen_export.schema import json_text

log = logging.getLogger(__name__)


def fingerprint(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def configuration_signature(config):
    return fingerprint(
        {
            "schema": SCHEMA_VERSION,
            "sources": config.sources.model_dump(),
            "output": config.output.model_dump(mode="json", exclude={"directory"}),
        }
    )


@dataclass
class RefreshCheck:
    checkpoints: dict = field(default_factory=dict)
    changed_contexts: set[str] | None = None
    dataportal_unchanged: bool = False
    eu_catalogs: dict | None = None
    eu_listings: dict = field(default_factory=dict)
    unchanged_eu: set[str] = field(default_factory=set)
    failures: dict = field(default_factory=dict)
    unchanged: bool = False


async def check_sources(client, config, previous):
    check = RefreshCheck()
    old = previous.get("checkpoints", {})
    started = now_iso()

    async def national():
        try:
            headers, counts = await asyncio.gather(
                client.get(DUMP, method="HEAD", accept="*/*"),
                client.context_counts(),
            )
            validators = {
                k: headers.headers[k]
                for k in ("etag", "last-modified", "content-length")
                if headers.headers.get(k)
            }
            prior = old.get("dataportal", {})
            if prior:
                # Inclusive overlap tolerates clock/indexing delays and equal timestamps.
                since = (
                    (datetime.fromisoformat(prior["checked_at"]) - timedelta(minutes=5))
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                modified = await client.context_counts(since=since)
                check.changed_contexts = set(modified) | {
                    context
                    for context in counts.keys() | prior["counts"].keys()
                    if counts.get(context) != prior["counts"].get(context)
                }
                previous_http = prior["http"]
                same_dump = bool(
                    validators.get("etag")
                    and validators.get("etag") == previous_http.get("etag")
                )
                if not validators.get("etag"):
                    same_dump = bool(
                        validators.get("last-modified")
                        and validators.get("content-length")
                        and validators == previous_http
                    )
                check.dataportal_unchanged = same_dump and not check.changed_contexts
            check.checkpoints["dataportal"] = {
                "checked_at": started,
                "counts": counts,
                "http": validators,
            }
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            StopIteration,
        ) as error:
            check.failures["dataportal"] = str(error)[:400]
            log.warning(
                "Dataportal change check unavailable; using normal retrieval: %s", error
            )

    async def europe():
        try:
            catalogs = {
                key: value
                for key, value in (
                    await client.eu_catalogs(config.sources.countries)
                ).items()
                if key not in config.sources.exclude_eu_catalogs
            }
            if not catalogs:
                raise ValueError("No additional EU catalogs discovered")
            check.eu_catalogs = catalogs
            checkpoints = {}

            async def one(catalog, stamp):
                try:
                    refs = await client.eu_listing(catalog)
                    check.eu_listings[catalog] = refs
                    current = {"modified": stamp, "listing": fingerprint(refs)}
                    checkpoints[catalog] = current
                    if (
                        stamp
                        and all(refs.values())
                        and old.get("dataeu", {}).get(catalog) == current
                    ):
                        check.unchanged_eu.add(catalog)
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                    check.failures[f"dataeu:{catalog}"] = str(error)[:400]

            await asyncio.gather(*(one(key, value) for key, value in catalogs.items()))
            if len(checkpoints) == len(catalogs):
                check.checkpoints["dataeu"] = checkpoints
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            check.failures["dataeu"] = str(error)[:400]
            log.warning(
                "EU change check unavailable; using normal retrieval: %s", error
            )

    tasks = []
    if config.sources.dataportal:
        tasks.append(national())
    if config.sources.dataeu:
        tasks.append(europe())
    await asyncio.gather(*tasks)
    check.unchanged = (
        not check.failures
        and (not config.sources.dataportal or check.dataportal_unchanged)
        and (
            not config.sources.dataeu
            or (
                set(check.eu_catalogs or {})
                == set(old.get("dataeu", {}))
                == check.unchanged_eu
            )
        )
    )
    return check
