# Swedish data.europa.eu export

A small repository-local scraper. Each harvest replaces
[`data/datasets.jsonl.gz`](data/datasets.jsonl.gz) with complete search records.
The optional [`data/catalog.db.gz`](data/catalog.db.gz) is rebuilt from that file.

## Run

```sh
uv sync --locked
uv run python scripts/scrape.py
uv run python scripts/build_database.py
```

The database command works independently of `database.enabled`. It needs an existing
gzip JSONL file and makes no network requests. Both scripts stream their input;
failed runs retain the previous completed download.

## Configuration

All operational settings live in [`config.toml`](config.toml):

| Setting | Meaning |
| --- | --- |
| `scrape.catalogs` | Catalog IDs; defaults to `[]` |
| `scrape.countries` | Country IDs; defaults to `["se"]` (all Swedish catalogs) |
| `scrape.languages` | Language-map keys to retain; defaults to `["sv", "en"]`; `[]` keeps all |
| `scrape.page_size` | Results per search page; defaults to 500 |
| `scrape.max_attempts` | Total attempts per request, including the initial attempt; defaults to 5 |
| `scrape.timeout_seconds` | Request timeout; defaults to 120 seconds |
| `scrape.retry_backoff_seconds` | Initial retry delay, doubled for subsequent failures |
| `scrape.page_delay_seconds` | Delay between successful pages; defaults to 0 |
| `scrape.gzip_level` | JSONL gzip compression level, 0–9; defaults to 6 |
| `scrape.log_level` | Python logging level; defaults to INFO |
| `schedule.enabled` | Enable automatic harvesting; manual runs still work |
| `schedule.interval_days` | Minimum local calendar days between successful JSONL publications |
| `schedule.time` | Local daily time, `HH:MM`; defaults to `06:00` |
| `schedule.timezone` | IANA timezone; defaults to `Europe/Stockholm` |
| `database.enabled` | Also rebuild SQLite during Harvest; defaults to true |
| `database.gzip_level` | SQLite gzip compression level; defaults to 6 |

Catalog and country selections form a **union**. A dataset matching both is written
once, using its API dataset ID. At least one selection is required.

The scraper requests complete records, without an `includes` projection. It follows
the search scroll to the end, including updated scroll IDs. Only language-map
filtering changes response contents; other nested metadata and unfamiliar fields
remain in each record. It does not download the underlying dataset files.

## GitHub Actions

- **Harvest:** runs once daily at the configured local time, or on manual dispatch.
  It fully scrapes the configured scope and optionally rebuilds SQLite.
- **Rebuild database:** manual dispatch; rebuilds SQLite from the checked-out gzip
  JSONL regardless of `database.enabled`. Missing input fails with an explicit message.
- **Checks:** Ruff, fixture tests, and verification that cron matches the configuration.

GitHub's native timezone-aware cron handles Stockholm daylight-saving changes.
After changing the configured time or timezone, run this and commit the workflow too:

```sh
python scripts/scrape.py --sync-schedule
```

The daily workflow checks the configured interval against the latest commit touching
the JSONL. Skipped days install no harvest dependencies and make no data.europa.eu
requests. Manual dispatch bypasses the automatic schedule.

Both publishing workflows share one concurrency group and use normal pushes. A
concurrent branch update causes publication to fail rather than overwrite that work.
If the optional database build fails, the completed JSONL still publishes and the
workflow remains failed. When automatic database rebuilding is disabled, an existing
database retains its previous contents until manually rebuilt. Git history shows
the separate publication times.

## SQLite

[`database.schema.sql`](database.schema.sql) defines five tables: `catalogs`,
`publishers`, `datasets`, `distributions`, and `data_services`. Its 47 structured
columns explicitly declare `JSON`. Language maps use keys such as `sv` and `en`.
Keywords, categories, contacts, temporal coverage, creators, landing pages, and links
are embedded JSON collections. Distribution URLs are one JSON object. Geographic references, coordinate reference systems, topic categories, applicable legislation,
series memberships, full service contacts and served dataset references are directly
queryable JSON columns. Catalogs and distributions also expose their complete
`record_json`, so their nested metadata is accessible without joining back to datasets.

Distribution and service row IDs are local integer keys. `distributions.source_id`
preserves the source identifier, including distributions shared by multiple parents.
Publisher resource URIs identify publisher rows; agents without one remain in the
dataset's complete `record_json` rather than being merged by name. Scalar query
columns project source codes/labels; `datasets.record_json` preserves the complete
language-filtered API record, including all nested distributions and services.

Decompress SQLite before opening it:

```python
import gzip
import shutil

with (
    gzip.open("data/catalog.db.gz", "rb") as source,
    open("catalog.db", "wb") as target,
):
    shutil.copyfileobj(source, target)
```

```sql
SELECT id, json_extract(title, '$.sv') AS title_sv,
       json_array_length(contact_points) AS contacts
FROM datasets;

SELECT d.id, r.format, json_extract(r.urls, '$.download') AS downloads
FROM datasets AS d
JOIN distributions AS r ON r.dataset_resource = d.resource;
```

Source license, rights, and attribution metadata remain in the exported records.
The repository's code license is in `LICENSE`.

## Development

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use mocked external HTTP pages and real gzip and SQLite files.
