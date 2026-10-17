"""Create databases once; later publications update only changed rows."""

import logging
import sqlite3
import tempfile
from pathlib import Path

import duckdb

from dataportalen_export.schema import json_text, populate

log = logging.getLogger(__name__)


def write_sqlite(path, tables, *, previous=None):
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        connection.execute("PRAGMA defer_foreign_keys=ON")
        changes = populate(connection, tables, previous=previous)
        connection.commit()
        log.info("SQLite changed rows: %s", changes)
        return changes
    finally:
        connection.close()


def write_parquet(directory, tables, changed, *, memory_limit_mb=512):
    """DuckDB is only an in-memory Parquet writer, not another database export."""
    directory.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="parquet-import-") as temporary,
        duckdb.connect(
            config={"memory_limit": f"{memory_limit_mb}MB", "threads": "1"}
        ) as connection,
    ):
        for table in sorted(changed):
            columns, _, rows = tables[table]
            definition = ",".join(
                f'"{name}" {sql.split(" REFERENCES")[0]}' for name, sql in columns
            )
            connection.execute(f'CREATE TABLE "{table}" ({definition})')
            path = Path(temporary) / "rows.jsonl"
            maximum = 0
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                for row in rows:
                    line = (
                        json_text(dict(zip((name for name, _ in columns), row))) + "\n"
                    )
                    maximum = max(maximum, len(line.encode("utf-8")))
                    stream.write(line)
            source = path.as_posix().replace("'", "''")
            column_types = ",".join(
                f"'{name}':'{sql.split()[0]}'" for name, sql in columns
            )
            if rows:
                connection.execute(
                    f"INSERT INTO \"{table}\" SELECT * FROM read_json('{source}', format='newline_delimited', maximum_object_size={maximum + 1}, columns={{{column_types}}})"
                )
            target = (directory / f"{table}.parquet").as_posix().replace("'", "''")
            connection.execute(
                f"COPY \"{table}\" TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 2048)"
            )
            connection.execute(f'DROP TABLE "{table}"')
