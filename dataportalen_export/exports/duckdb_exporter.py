import tempfile
from pathlib import Path

import duckdb

from dataportalen_export.schema import populate


class DuckdbExporter:
    def write(self, path, tables, *, parquet_directory=None, memory_limit_mb=512):
        with (
            tempfile.TemporaryDirectory(prefix="duckdb-import-") as temporary,
            duckdb.connect(
                str(path) if path else ":memory:",
                config={"memory_limit": f"{memory_limit_mb}MB", "threads": "1"},
            ) as connection,
        ):
            # Keep text available to the outer XZ dictionary instead of encoding
            # it separately with FSST. Schema, values, and indexes are unchanged.
            connection.execute("PRAGMA disabled_compression_methods='fsst'")
            connection.execute("BEGIN")
            populate(connection, tables, bulk_directory=Path(temporary))
            connection.execute("COMMIT")
            if parquet_directory:
                parquet_directory.mkdir(parents=True, exist_ok=True)
                for table in tables:
                    target = str(parquet_directory / f"{table}.parquet").replace(
                        "'", "''"
                    )
                    connection.execute(
                        f"COPY \"{table}\" TO '{target}' "
                        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 2048)"
                    )
            if path:
                connection.execute("CHECKPOINT")
