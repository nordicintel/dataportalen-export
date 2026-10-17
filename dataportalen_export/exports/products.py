"""Publish accepted products together; retain unchanged files without reopening them."""

import gzip
import json
import logging
import lzma
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path

import duckdb
from rdflib import Dataset

from dataportalen_export.current_state import SCHEMA_VERSION, CurrentState
from dataportalen_export.exports.databases import write_parquet, write_sqlite
from dataportalen_export.exports.jsonl_exporter import (
    catalog_names,
    jsonl_products,
    write_jsonl,
)
from dataportalen_export.exports.jsonld_exporter import catalog_records, write_jsonld
from dataportalen_export.refresh import configuration
from dataportalen_export.schema import json_text, tables_for

log = logging.getLogger(__name__)


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


def unpack(source, target):
    opener = (
        lzma.open
        if source.suffix == ".xz"
        else gzip.open
        if source.suffix == ".gz"
        else open
    )
    with opener(source, "rb") as incoming, target.open("wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)


def source_groups(state, names):
    groups = defaultdict(dict)
    for key, record in sorted(state.records.items()):
        catalogs = record.catalog_ids or [
            e["id"] for e in record.entities if e["kind"] == "catalogs"
        ]
        catalog = next(
            (
                names[identifier]
                for identifier in sorted(catalogs)
                if identifier in names
            ),
            "unresolved",
        )
        groups[f"source_records/{record.source}/{catalog}.jsonl"][key] = record
    return groups


def write_products(
    state,
    config,
    batches,
    *,
    attempted_at,
    raw_dump=None,
    checkpoints=None,
    previous=None,
):
    output = config.output.directory.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    if output.exists() and any(output.iterdir()) and not manifest_path.exists():
        raise ValueError("Refusing to replace an unmanaged nonempty output directory")
    old_manifest = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    )
    previous = previous if previous is not None else CurrentState.load(output)
    entities, links, provenance = state.materialize()
    if not entities["datasets"]:
        raise ValueError("No usable datasets; refusing empty publication")
    old_entities, old_links, old_provenance = previous.materialize()
    names = catalog_names(entities["catalogs"])
    groups = source_groups(state, names)
    old_groups = source_groups(previous, catalog_names(old_entities["catalogs"]))
    storage_keys = ("compression", "compression_level", "compression_threshold_mib")
    old_settings = old_manifest.get("configuration", {}).get("output", {})
    storage_matches = all(
        old_settings.get(key, getattr(config.output, key))
        == getattr(config.output, key)
        for key in storage_keys
    )
    with tempfile.TemporaryDirectory(prefix=".export-", dir=output.parent) as temporary:
        root = Path(temporary)
        candidate = root / "data"
        candidate.mkdir()
        retained = set()

        def old_file(name):
            return next(
                (
                    output / (name + suffix)
                    for suffix in ("", ".xz", ".gz")
                    if (output / (name + suffix)).is_file()
                ),
                None,
            )

        def reuse(name, unchanged):
            old = old_file(name) if unchanged and storage_matches else None
            if old:
                relative = old.relative_to(output).as_posix()
                retain_file(old, candidate / relative)
                retained.add(relative)
            return bool(old)

        log.info(
            "Writing %d source groups; unchanged groups retain their files", len(groups)
        )
        for name, records in groups.items():
            if not reuse(name, records == old_groups.get(name)):
                path = candidate / name
                write_jsonl(path, (vars(record) for record in records.values()))
                compress_file(
                    path, config.output.compression, config.output.compression_level
                )
        log.info("Projecting six object tables")
        tables = tables_for(state, entities, links, provenance)
        old_tables = (
            tables_for(previous, old_entities, old_links, old_provenance)
            if old_manifest.get("schema_version") == SCHEMA_VERSION
            else None
        )
        changes = {}
        if config.output.sqlite and not reuse("catalog.db", old_tables == tables):
            target = candidate / "catalog.db"
            old = old_file("catalog.db") if old_tables is not None else None
            if old:
                unpack(old, target)
            changes = write_sqlite(target, tables, previous=old_tables if old else None)
        changed_parquet = {
            table
            for table in tables
            if config.output.parquet
            and not reuse(
                f"parquet/{table}.parquet",
                old_tables is not None and tables[table] == old_tables[table],
            )
        }
        if changed_parquet:
            write_parquet(
                candidate / "parquet",
                tables,
                changed_parquet,
                memory_limit_mb=config.output.duckdb_memory_limit_mb,
            )
        old_jsonl = dict(
            jsonl_products(old_entities, old_links, old_provenance, config.output)
        )
        for name, rows in jsonl_products(entities, links, provenance, config.output):
            if not reuse(name, rows == old_jsonl.get(name)):
                write_jsonl(candidate / name, rows)
        if config.output.jsonld:
            old_catalogs = catalog_records(
                previous, old_entities, old_links, old_provenance
            )
            for catalog, records in catalog_records(
                state, entities, links, provenance
            ).items():
                unchanged = records == old_catalogs.get(catalog) and all(
                    state.records[key].digest == previous.records[key].digest
                    for key in records
                )
                name = f"jsonld/{names[catalog]}.jsonld"
                if not reuse(name, unchanged):
                    write_jsonld(candidate / name, state, records)
        old_raw = output / "raw/dataportal.rdf.gz"
        raw_dump = raw_dump or (old_raw if old_raw.exists() else None)
        if raw_dump:
            target = candidate / "raw/dataportal.rdf.gz"
            retain_file(raw_dump, target)
            if raw_dump.resolve() == old_raw.resolve():
                retained.add("raw/dataportal.rdf.gz")
        omissions = []
        for path in sorted(candidate.rglob("*")):
            if not path.is_file():
                continue
            if (
                path.stat().st_size > config.output.compression_threshold_mib * 1024** 2
                and path.suffix not in {".gz", ".xz"}
            ):
                path = compress_file(
                    path, config.output.compression, config.output.compression_level
                )
            if path.stat().st_size >= 100 * 1024**2:
                if path.parent == candidate and path.name in {
                    "datasets.jsonl.xz",
                    "datasets.jsonl.gz",
                }:
                    omissions.append(
                        "Combined datasets.jsonl exceeds GitHub limit; use catalog files"
                    )
                    path.unlink()
                else:
                    raise ValueError(
                        f"Required export exceeds GitHub limit: {path.relative_to(candidate)} ({path.stat().st_size:,} bytes)"
                    )
        failures = {
            key: value for batch in batches for key, value in batch.failures.items()
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "attempted_at": attempted_at,
            "configuration": configuration(config),
            "checkpoints": checkpoints or {},
            "no_changes": False,
            "status": "source_failure"
            if any(not b.successful_scopes for b in batches)
            else "warnings"
            if failures or omissions
            else "success",
            "sources": [
                {
                    "source": b.source,
                    "http": b.http,
                    "successful_scopes": sorted(b.successful_scopes),
                    "complete_scopes": sorted(b.complete_scopes),
                    "enumerated_records": len(b.seen),
                    "accepted_records": len(b.records),
                    "fetched": b.fetched,
                    "reused": b.reused,
                    "failure_count": len(b.failures),
                }
                for b in batches
            ],
            "entity_counts": {kind: len(rows) for kind, rows in entities.items()},
            "table_counts": {table: len(spec[2]) for table, spec in tables.items()},
            "record_count": len(state.records),
            "referenced_records": state.referenced_records,
            "stale_records": sum(r.stale for r in state.records.values()),
            "deleted_records": state.deleted,
            "datasets_without_catalog": len(
                set(entities["datasets"])
                - {right for table, _, right in links if table == "catalog_datasets"}
            ),
            "failures": failures,
            "omissions": omissions,
            "reused_files": len(retained),
            "sqlite_changed_rows": changes,
            "source_shards": {
                name + (".xz" if config.output.compression == "xz" else ".gz"): len(
                    records
                )
                for name, records in groups.items()
            },
            "files": {
                path.relative_to(candidate).as_posix(): {"bytes": path.stat().st_size}
                for path in sorted(candidate.rglob("*"))
                if path.is_file()
            },
        }
        manifest["catalog_files"] = {
            identifier: {
                kind: next(
                    (
                        path + suffix
                        for suffix in ("", ".gz", ".xz")
                        if path + suffix in manifest["files"]
                    ),
                    None,
                )
                for kind, path in (
                    ("jsonl", f"catalogs/{name}.jsonl"),
                    ("jsonld", f"jsonld/{name}.jsonld"),
                )
            }
            for identifier, name in names.items()
        }
        (candidate / "manifest.json").write_text(
            json_text(manifest) + "\n", encoding="utf-8"
        )
        log.info(
            "Validating %d changed products; retaining %d products",
            len(manifest["files"]) - len(retained),
            len(retained),
        )
        validate_products(candidate, unchanged_files=retained)
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
            if path.stat().st_size != info["bytes"]:
                raise ValueError(f"Product size mismatch: {name}")
            if name in unchanged_files:
                continue
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
