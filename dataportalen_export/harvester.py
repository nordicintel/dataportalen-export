import json
import logging
import tempfile
from pathlib import Path

from dataportalen_export.current_state import CurrentState, now_iso
from dataportalen_export.dataeu_source import DataEuSource
from dataportalen_export.dataportal_source import DataportalSource
from dataportalen_export.exports.products import write_products
from dataportalen_export.portal_client import PortalClient

log = logging.getLogger(__name__)


class Harvester:
    async def run(self, config, *, force=False):
        attempted_at = now_iso()
        state = CurrentState.load(config.output.directory)
        manifest_path = config.output.directory / "manifest.json"
        previous_manifest = (
            json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        )
        batches = []
        with tempfile.TemporaryDirectory(prefix="dataportalen-harvest-") as temporary:
            raw = None
            async with PortalClient(config.harvest) as client:
                if config.sources.dataportal:
                    batch, raw = await DataportalSource().harvest(
                        client,
                        state.records,
                        Path(temporary),
                        force=force,
                        cached_dump=config.output.directory / "raw/dataportal.rdf.gz",
                        previous_status=next(
                            (
                                s
                                for s in previous_manifest.get("sources", [])
                                if s["source"] == "dataportal"
                            ),
                            {},
                        ),
                    )
                    state.apply(batch)
                    batches.append(batch)
                if config.sources.dataeu:
                    batch = await DataEuSource().harvest(
                        client, state.records, config.sources, force=force
                    )
                    state.apply(batch)
                    batches.append(batch)
            if not any(batch.successful_scopes for batch in batches):
                raise RuntimeError(
                    f"No source refreshed successfully: {[batch.failures for batch in batches]}"
                )
            manifest = write_products(
                state, config, batches, attempted_at=attempted_at, raw_dump=raw
            )
        log.info(
            "Published current products: %s; stale=%d failures=%d",
            manifest["entity_counts"],
            manifest["stale_records"],
            len(manifest["failures"]),
        )
        return manifest
