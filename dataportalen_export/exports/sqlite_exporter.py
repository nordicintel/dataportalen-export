import sqlite3

from dataportalen_export.schema import populate


class SqliteExporter:
    def write(self, path, tables):
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN")
            populate(connection, tables)
            connection.commit()
            if (
                connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or connection.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("SQLite integrity validation failed")
        finally:
            connection.close()
