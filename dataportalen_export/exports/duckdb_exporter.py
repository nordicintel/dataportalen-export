import duckdb

from dataportalen_export.schema import populate


class DuckdbExporter:
    def write(self, path, tables, *, parquet_directory=None):
        with duckdb.connect(str(path) if path else ":memory:") as connection:
            connection.execute("BEGIN")
            populate(connection, tables)
            connection.execute("COMMIT")
            if parquet_directory:
                parquet_directory.mkdir(parents=True, exist_ok=True)
                for table in tables:
                    target = str(parquet_directory / f"{table}.parquet").replace(
                        "'", "''"
                    )
                    connection.execute(
                        f"COPY \"{table}\" TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
            if path:
                connection.execute("CHECKPOINT")
