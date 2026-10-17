import json
import logging
import os
import tempfile
from pathlib import Path

from dataportalen_export.current_state import (
    SCHEMA_VERSION,
    CurrentState,
    SourceBatch,
    now_iso,
)
from dataportalen_export.dataeu_source import DataEuSource
from dataportalen_export.dataportal_source import DataportalSource
from dataportalen_export.exports.products import write_products
from dataportalen_export.portal_client import PortalClient
from dataportalen_export.refresh import check_sources, configuration_signature
from dataportalen_export.schema import json_text

log = logging.getLogger(__name__)


class Harvester:
    def __init__(self, *, transport=None):
        self.transport = transport

    async def run(self, config, *, force=False):
        attempted_at = now_iso()
        manifest_path = config.output.directory / "manifest.json"
        previous_manifest = (
            json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        )
        batches = []
        async with PortalClient(config.harvest, transport=self.transport) as client:
            check = await check_sources(client, config, previous_manifest)
            compatible = previous_manifest.get(
                "schema_version"
            ) == SCHEMA_VERSION and previous_manifest.get(
                "configuration_signature"
            ) == configuration_signature(config)
            healthy = previous_manifest.get(
                "status"
            ) == "success" and not previous_manifest.get("failures")
            if (
                not force
                and compatible
                and healthy
                and check.unchanged
                and previous_manifest.get("files")
                and all(
                    (config.output.directory / name).is_file()
                    and (config.output.directory / name).stat().st_size == info["bytes"]
                    for name, info in previous_manifest["files"].items()
                )
            ):
                manifest = dict(previous_manifest)
                manifest.update(
                    attempted_at=attempted_at,
                    checkpoints=check.checkpoints,
                    no_changes=True,
                    deleted_records=0,
                    reused_files=len(manifest["files"]),
                )
                manifest["sources"] = [
                    {**source, "fetched": 0, "reused": source["accepted_records"]}
                    for source in manifest["sources"]
                ]
                temporary_manifest = manifest_path.with_suffix(".json.tmp")
                temporary_manifest.write_text(
                    json_text(manifest) + "\n", encoding="utf-8"
                )
                os.replace(temporary_manifest, manifest_path)
                log.info(
                    "No source changes: skipped state loading, retrieval, export construction and validation"
                )
                return manifest
            state = CurrentState.load(config.output.directory)
            with tempfile.TemporaryDirectory(
                prefix="dataportalen-harvest-"
            ) as temporary:
                raw = None
                if config.sources.dataportal:
                    previous_status = next(
                        (
                            s
                            for s in previous_manifest.get("sources", [])
                            if s["source"] == "dataportal"
                        ),
                        {},
                    )
                    source_healthy = bool(previous_status) and not previous_status.get(
                        "failure_count"
                    )
                    if not force and source_healthy and check.dataportal_unchanged:
                        batch = retained_batch("dataportal", state, previous_status)
                    else:
                        batch, raw = await DataportalSource().harvest(
                            client,
                            state.records,
                            Path(temporary),
                            force=force,
                            cached_dump=config.output.directory
                            / "raw/dataportal.rdf.gz",
                            previous_status=previous_status,
                            changed_contexts=check.changed_contexts
                            if source_healthy and not force
                            else None,
                        )
                    state.apply(batch)
                    batches.append(batch)
                if config.sources.dataeu:
                    batch = await DataEuSource().harvest(
                        client,
                        state.records,
                        config.sources,
                        force=force,
                        check=check if not force else None,
                    )
                    state.apply(batch)
                    batches.append(batch)
                if not any(batch.successful_scopes for batch in batches):
                    raise RuntimeError(
                        f"No source refreshed successfully: {[batch.failures for batch in batches]}"
                    )
                checkpoints = {
                    source: value
                    for source, value in check.checkpoints.items()
                    if any(b.source == source and not b.failures for b in batches)
                }
                manifest = write_products(
                    state,
                    config,
                    batches,
                    attempted_at=attempted_at,
                    raw_dump=raw,
                    checkpoints=checkpoints,
                )
        log.info(
            "Published current products: %s; stale=%d failures=%d",
            manifest["entity_counts"],
            manifest["stale_records"],
            len(manifest["failures"]),
        )
        return manifest


def retained_batch(source, state, status):
    records = {
        key: record for key, record in state.records.items() if record.source == source
    }
    return SourceBatch(
        source,
        http=status.get("http", {}),
        records=records,
        seen=set(records),
        reused=len(records),
        complete_scopes=set(status["complete_scopes"]),
        successful_scopes=set(status["successful_scopes"]),
    )
