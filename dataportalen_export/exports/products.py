"""Build, validate, and replace a complete set of current products."""

import gzip
import hashlib
import json
import logging
import lzma
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import duckdb
from rdflib import Dataset

from dataportalen_export.current_state import SCHEMA_VERSION
from dataportalen_export.exports.duckdb_exporter import DuckdbExporter
from dataportalen_export.exports.jsonl_exporter import JsonlExporter, rows_digest
from dataportalen_export.exports.jsonld_exporter import JsonldExporter
from dataportalen_export.exports.sqlite_exporter import SqliteExporter
from dataportalen_export.refresh import configuration_signature
from dataportalen_export.schema import json_text, tables_for

log = logging.getLogger(__name__)


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compress_file(path, method, level):
    target = path.with_name(path.name + (".xz" if method == "xz" else ".gz"))
    with path.open("rb") as source:
        if method == "xz":
            compressed = lzma.open(target, "wb", preset=level)
        else:
            compressed = gzip.GzipFile(
                filename=str(target), mode="wb", mtime=0, compresslevel=level
            )
        with compressed:
            shutil.copyfileobj(source, compressed)
    path.unlink()
    return target


def retain_file(source, target):
    """Keep accepted bytes without reading/copying them when on the same filesystem."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def write_products(
    state, config, batches, *, attempted_at, raw_dump=None, checkpoints=None
):
    output = config.output.directory.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if (
        output.exists()
        and any(output.iterdir())
        and not (output / "manifest.json").exists()
    ):
        raise ValueError("Refusing to replace an unmanaged nonempty output directory")
    # Hosted failures retain their candidate for the short-lived diagnostic artifact.
    # Local runs clean up normally; hosted runners discard their workspace afterward.
    with tempfile.TemporaryDirectory(
        prefix=".export-",
        dir=output.parent,
        delete=os.environ.get("GITHUB_ACTIONS") != "true",
    ) as temporary:
        root = Path(temporary).resolve()
        candidate = root / "data"
        candidate.mkdir()
        entities, links, provenance = state.materialize()
        if not entities["datasets"]:
            raise ValueError("No usable datasets; refusing empty publication")
        tables = tables_for(state, entities, links, provenance)
        log.info(
            "Writing products: %s", {kind: len(rows) for kind, rows in entities.items()}
        )
        raw_records = candidate / "source_records.jsonl"
        with raw_records.open("w", encoding="utf-8", newline="\n") as stream:
            for record in sorted(state.records.values(), key=lambda r: r.id):
                stream.write(json_text(record.model_dump()) + "\n")
        previous_manifest = (
            json.loads((output / "manifest.json").read_text())
            if (output / "manifest.json").exists()
            else {}
        )
        settings_match = previous_manifest.get("configuration_signature") in (
            None,
            configuration_signature(config),
        )
        retained_files = set()
        state_digest = checksum(raw_records)
        previous_state_digest = previous_manifest.get("state_digest")
        old_state = output / (
            "source_records.jsonl."
            + ("xz" if config.output.compression == "xz" else "gz")
        )
        if old_state.exists() and previous_state_digest is None:
            opener = lzma.open if old_state.suffix == ".xz" else gzip.open
            with opener(old_state, "rb") as stream:
                previous_state_digest = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
        state_reused = (
            settings_match
            and old_state.exists()
            and previous_state_digest == state_digest
        )
        if state_reused:
            state_file = candidate / old_state.name
            retain_file(old_state, state_file)
            retained_files.add(old_state.name)
            raw_records.unlink()
        else:
            state_file = compress_file(
                raw_records, config.output.compression, config.output.compression_level
            )
        content_digest = checksum(state_file)
        reusable = (
            previous_manifest.get("schema_version") == SCHEMA_VERSION
            and settings_match
            and previous_state_digest == state_digest
            and previous_manifest.get("referenced_records", {})
            == state.referenced_records
        )

        product_digests = {}
        reused_files = int(state_reused)

        def reuse(name, digest=None):
            nonlocal reused_files
            if digest is not None:
                product_digests[name] = digest
            matches = (
                digest is not None
                and settings_match
                and previous_manifest.get("schema_version") == SCHEMA_VERSION
                and previous_manifest.get("product_digests", {}).get(name) == digest
            )
            if reusable or matches:
                for suffix in ("", ".gz", ".xz"):
                    old = output / (name + suffix)
                    if old.exists():
                        target = candidate / (name + suffix)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        retain_file(old, target)
                        retained_files.add((name + suffix))
                        reused_files += 1
                        return True
            return False

        if config.output.sqlite and not reuse("catalog.db"):
            log.info("Writing SQLite")
            SqliteExporter().write(candidate / "catalog.db", tables)
        duck_reused = config.output.duckdb and reuse("catalog.duckdb")
        changed_parquet = set()
        if config.output.parquet:
            for table, (columns, constraints, rows) in tables.items():
                digest = rows_digest((columns, constraints, *rows))
                if not reuse(f"parquet/{table}.parquet", digest):
                    changed_parquet.add(table)
        if (config.output.duckdb and not duck_reused) or changed_parquet:
            log.info("Writing DuckDB/Parquet")
            DuckdbExporter().write(
                candidate / "catalog.duckdb"
                if config.output.duckdb and not duck_reused
                else None,
                tables,
                parquet_directory=candidate / "parquet" if changed_parquet else None,
                parquet_tables=changed_parquet,
                memory_limit_mb=config.output.duckdb_memory_limit_mb,
            )
        JsonlExporter().write(
            candidate, entities, links, provenance, config.output, reuse=reuse
        )
        if config.output.jsonld:
            log.info("Writing JSON-LD")
            JsonldExporter().write(
                candidate / "jsonld", state, entities, links, provenance, reuse=reuse
            )
        previous_raw = output / "raw/dataportal.rdf.gz"
        raw_dump = raw_dump or (previous_raw if previous_raw.exists() else None)
        if raw_dump:
            (candidate / "raw").mkdir()
            if raw_dump.resolve() == previous_raw.resolve():
                retain_file(raw_dump, candidate / "raw/dataportal.rdf.gz")
                retained_files.add("raw/dataportal.rdf.gz")
            else:
                shutil.copyfile(raw_dump, candidate / "raw/dataportal.rdf.gz")
        omissions = []
        log.info("Reused %d unchanged product files", reused_files)
        for path in sorted(candidate.rglob("*")):
            if not path.is_file():
                continue
            if (
                path.stat().st_size > config.output.compression_threshold_mib * 1024** 2
                and path.suffix not in {".gz", ".xz"}
            ):
                log.info(
                    "Compressing %s with %s",
                    path.relative_to(candidate),
                    config.output.compression,
                )
                path = compress_file(
                    path, config.output.compression, config.output.compression_level
                )
            if path.stat().st_size >= 100 * 1024**2:
                if (
                    path.name in {"datasets.jsonl.gz", "datasets.jsonl.xz"}
                    and path.parent == candidate
                ):
                    omissions.append(
                        "Combined datasets.jsonl exceeds GitHub limit after compression; use catalog splits"
                    )
                    path.unlink()
                else:
                    raise ValueError(
                        f"Required export exceeds GitHub limit: {path.relative_to(candidate)} "
                        f"({path.stat().st_size:,} bytes; limit {100 * 1024**2:,})"
                    )
        failures = {
            key: value for batch in batches for key, value in batch.failures.items()
        }
        outcomes = [
            {
                "source": batch.source,
                "http": batch.http,
                "successful_scopes": sorted(batch.successful_scopes),
                "complete_scopes": sorted(batch.complete_scopes),
                "enumerated_records": len(batch.seen),
                "accepted_records": len(batch.records),
                "fetched": batch.fetched,
                "reused": batch.reused,
                "failure_count": len(batch.failures),
            }
            for batch in batches
        ]
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "attempted_at": attempted_at,
            "configuration_signature": configuration_signature(config),
            "checkpoints": checkpoints or {},
            "no_changes": False,
            "status": "source_failure"
            if any(not batch.successful_scopes for batch in batches)
            else "warnings"
            if failures or omissions
            else "success",
            "sources": outcomes,
            "content_digest": content_digest,
            "state_digest": state_digest,
            "product_digests": product_digests,
            "reused_files": reused_files,
            "entity_counts": {kind: len(rows) for kind, rows in entities.items()},
            "table_counts": {table: len(spec[2]) for table, spec in tables.items()},
            "record_count": len(state.records),
            "referenced_records": state.referenced_records,
            "stale_records": sum(r.stale for r in state.records.values()),
            "datasets_without_catalog": len(
                set(entities["datasets"])
                - {right for table, left, right in links if table == "catalog_datasets"}
            ),
            "deleted_records": state.deleted,
            "failures": failures,
            "omissions": omissions,
            "files": {},
        }
        for path in sorted(candidate.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(candidate).as_posix()
            prior = previous_manifest.get("files", {}).get(name)
            if (
                name in retained_files
                and prior
                and path.stat().st_size == prior["bytes"]
            ):
                manifest["files"][name] = prior
            else:
                retained_files.discard(name)
                manifest["files"][name] = {
                    "bytes": path.stat().st_size,
                    "sha256": checksum(path),
                }
        (candidate / "manifest.json").write_text(
            json_text(manifest) + "\n", encoding="utf-8"
        )
        log.info(
            "Validating %d changed products; retaining %d previously validated products",
            len(manifest["files"]) - len(retained_files),
            len(retained_files),
        )
        validate_products(candidate, unchanged_files=retained_files)
        # Both moves stay within the configured output parent and our owned temp directory.
        backup = root / "previous"
        if output.exists():
            os.replace(output, backup)
        try:
            os.replace(candidate, output)
        except OSError:
            if backup.exists():
                os.replace(backup, output)
            raise
        return manifest


def validate_products(directory: Path, *, unchanged_files=frozenset()):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="validate-") as temporary:
        for name, info in manifest["files"].items():
            path = directory / name
            if name in unchanged_files:
                if path.stat().st_size != info["bytes"]:
                    raise ValueError(f"Retained product size mismatch: {name}")
                continue
            if path.stat().st_size != info["bytes"] or checksum(path) != info["sha256"]:
                raise ValueError(f"Checksum mismatch: {name}")
            original_name = name.removesuffix(".gz").removesuffix(".xz")
            opener = (
                lzma.open
                if name.endswith(".xz")
                else gzip.open
                if name.endswith(".gz")
                else open
            )
            if name.startswith("raw/"):
                with opener(path, "rb") as stream:
                    while stream.read(1024 * 1024):
                        pass
                continue
            if name.endswith((".gz", ".xz")) and original_name.endswith(
                (".db", ".duckdb", ".parquet", ".jsonld")
            ):
                unpacked = Path(temporary) / Path(original_name).name
                with opener(path, "rb") as source, unpacked.open("wb") as target:
                    shutil.copyfileobj(source, target)
                path = unpacked
            if original_name.endswith(".db"):
                connection = sqlite3.connect(
                    f"file:{path.as_posix()}?mode=ro", uri=True
                )
                try:
                    if (
                        connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                        or connection.execute("PRAGMA foreign_key_check").fetchall()
                    ):
                        raise ValueError("SQLite integrity failed")
                    for table, count in manifest["table_counts"].items():
                        if (
                            connection.execute(
                                f'SELECT count(*) FROM "{table}"'
                            ).fetchone()[0]
                            != count
                        ):
                            raise ValueError(f"SQLite count mismatch: {table}")
                finally:
                    connection.close()
            elif original_name.endswith(".duckdb"):
                with duckdb.connect(str(path), read_only=True) as connection:
                    for table, count in manifest["table_counts"].items():
                        if (
                            connection.execute(
                                f'SELECT count(*) FROM "{table}"'
                            ).fetchone()[0]
                            != count
                        ):
                            raise ValueError(f"DuckDB count mismatch: {table}")
            elif original_name.endswith(".parquet"):
                with duckdb.connect() as connection:
                    cursor = connection.execute(
                        "SELECT * FROM read_parquet(?)", [str(path)]
                    )
                    count = 0
                    while rows := cursor.fetchmany(1000):
                        count += len(rows)
                    if count != manifest["table_counts"][Path(original_name).stem]:
                        raise ValueError(f"Parquet count mismatch: {name}")
            elif original_name.endswith(".jsonld"):
                Dataset().parse(path, format="json-ld")
            elif original_name.endswith(".jsonl"):
                with opener(path, "rt", encoding="utf-8") as stream:
                    for line in stream:
                        json.loads(line)
    return manifest
