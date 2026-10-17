# Issues

Issue conventions: `docs/agents/issue-tracker.md`.

## ISSUE-001: Implement the daily Swedish catalog metadata exporter

State: open
Status: ready-for-agent
Assignee: Codex
Type: task

### Objective and agreed scope

Build a small, repo-local Python application from the supplied `dcat_dataset_export.py` reference, personally reviewed in full (2,449 lines). Harvest metadata from Dataportal and other Swedish catalogs in data.europa.eu. Treat the underlying catalogs as first-class objects; `oppnadata` is an aggregate source identifier, not a replacement for those catalogs.

Run in this repository's GitHub Actions. Write the current products back to `/data` in the repository. Run once daily, controlled by root `config.toml`, with an hourly Actions due check. No local scheduler, external storage, installable distribution, dated export snapshots, or separate EU quality-metrics fetching. Normal Git commit history remains; this plan does not rewrite repository history.

The target is unattended operation with isolated failures and minimal maintenance. No implementation can guarantee years of service availability or compatibility with external systems. Do not disguise an unusable harvest as success.

### Application structure and dependencies

Use one importable directory, `dataportalen_export/`, with a thin `__main__.py`. Invoke it from the repo using `uv run python -m dataportalen_export`. This is a repo application: no build backend, wheel, publishing metadata, console-script installation, or external Python distribution. Use `pyproject.toml` for dependencies and development tools, `tool.uv.package = false`, and commit `uv.lock`.

Proposed layout, creating files only as their implementation slice requires them:

```text
config.toml
pyproject.toml
uv.lock
dataportalen_export/
  __init__.py
  __main__.py
  config.py
  portal_client.py
  dataportal_source.py
  context_index.py
  dataeu_source.py
  rdf_converter.py
  identity.py
  current_state.py
  harvester.py
  schema.py
  schedule.py
  models/
    catalog.py
    dataset.py
    dataset_series.py
    distribution.py
    data_service.py
    publisher.py
    source_record.py
  exports/
    jsonl_exporter.py
    sqlite_exporter.py
    duckdb_exporter.py
    jsonld_exporter.py
tests/
  fixtures/
.github/workflows/
  checks.yml
  harvest.yml
data/
```

Put each substantial class in its own file. Keep simple transformations and identity rules as functions. Avoid plugin registries, abstract base-class hierarchies, a generic repository layer, and classes that only forward calls. The DuckDB exporter also writes Parquet, avoiding another implementation of tabular conversion.

Runtime dependencies: `httpx` for bounded asynchronous requests, `rdflib` for RDF parsing/serialization and graph comparison, `pydantic` for config and object contracts, and `duckdb` for its database and Parquet output. Use standard-library `sqlite3`, `tomllib`, `logging`, `gzip`, `hashlib`, and `zoneinfo`. Development dependencies start with `pytest` and Ruff. Use HTTPX's test transport for external HTTP fixtures rather than mocking internal classes. Do not add pandas, an ORM, a scheduler service, or a second dataframe engine without a demonstrated need.

### Data contract

Six principal object types have dedicated models, tables, and object-type JSONL exports: catalogs, datasets, dataset series, distributions, data services, and publishers. A publisher retains its actual RDF type, including `foaf:Agent`; the model must not falsely assert every publisher is an organization.

Common fields include stable `id`, original `uri` when present, original RDF types, multilingual text, source references, and complete structured metadata. Preserve all languages. Text values use language-to-list mappings, including `und` for untagged text; collection fields remain arrays for zero, one, or many items. Keep source language tags intact, including machine-translation tags. A convenience language preference must not delete languages.

Known fields receive explicit mappings. Unknown properties live under their full predicate IRIs in `extra_properties`, using explicit reference/literal values where necessary to retain datatype and language. Do not derive unknown keys by dropping namespaces. Keep invalid optional dates/numbers as original lexical values in retained metadata, emit a warning, and leave the corresponding typed projection null. Missing optional metadata must not reject an otherwise identifiable object.

Preserve original fetched RDF and distinct source variants. JSON-LD comes from retained RDF, not a reverse conversion of simplified JSON. Use a bundled context and graph serialization that preserves typed literals, language tags, references, cycles, and blank-node distinctions; loading an export must not require a remote context request.

Identity rules:

- Catalog identity uses the source-native catalog/context identifier and catalog URI, never its title. Keep context identity and catalog resource identity distinct when a context describes multiple resources.
- Dataset identity uses an exact original dataset URI when available. Do not lowercase URI paths, strip meaningful characters, or merge by title, publisher name, shared endpoint, or UUID alone.
- Use a source-qualified native ID when no original URI exists. Preserve ambiguous candidates separately.
- Derive filesystem-safe IDs with a stable digest rather than lossy URI slugification. Retain native IDs and URIs in the record.
- Link exact cross-source matches, retain every source variant and all memberships, and prefer the Dataportal version for the default structured view. Do not overwrite a complete alternate payload with an `also_in` summary.
- Shared blank nodes require source-scoped identities and topology-aware comparison. A blank-node relabeling must not look like changed content; a topology change must.
- An unresolved catalog membership stays explicitly unresolved; do not invent an `oppnadata` membership or discard the dataset. Report resolution gaps.

The combined dataset JSONL contains one canonical dataset per line with its full convenient dataset representation, linked catalog IDs, related entity details needed for standalone use, and provenance. Complete alternate payloads remain accessible in source records. Per-catalog dataset JSONL contains that catalog's members; repeated appearance across catalog files is intentional. Series have a separate object-type export and explicit membership links.

### Database and tabular contract

SQLite and DuckDB expose equivalent logical schemas. Parquet contains one file per logical table, with the same IDs and relationships. SQL column types may differ by engine, but meanings, row counts, null handling, and JSON content must agree.

| Table group | Required contents |
| --- | --- |
| `catalogs` | ID, URI, source/context identifiers, title/name projection, description, homepage, complete JSON |
| `datasets` | ID, URI, multilingual title/description JSON, issued/modified projections, access rights, complete JSON |
| `dataset_series` | ID, URI, multilingual text, date projections, complete JSON |
| `distributions` | ID, URI, access/download URL collections, media type, format, license, byte-size projection, complete JSON |
| `data_services` | ID, URI, endpoint URL/description collections, title, complete JSON |
| `publishers` | ID, URI, names, identifier collections, homepage, original types, complete JSON |
| Membership/link tables | Catalog-to-dataset, catalog-to-series, dataset-to-series, dataset-to-distribution, distribution-to-service, service-to-dataset, and publisher links for catalog/dataset/series |
| `source_records` | Source-qualified record ID, source/native identifiers, root entity kind/ID, modification signal, graph digest, last successful fetch, freshness, full source JSON and RDF |
| `entity_sources` | Entity kind/ID to source-record associations, preserving provenance for shared extracted entities |
| `harvest_status` | Current source/catalog outcomes and latest attempt/success times; no run-history table |

Use primary keys, explicit foreign keys on concrete link tables, uniqueness constraints on links, and indexes on identifiers and relationship columns. Validate polymorphic entity references in `source_records`/`entity_sources` against the six entity tables before publishing. A referenced external entity without descriptive metadata remains an external URI in JSON rather than a fabricated table row.

Document all columns, nullability, JSON fields, and example joins in README during implementation. Keep DDL and row projection definitions together in `schema.py`; use small explicit engine differences rather than an ORM or SQL-generation framework. Preserve multivalued fields rather than arbitrarily selecting the first publisher, URL, or identifier.

### Current files and retained state

```text
data/
  datasets.jsonl
  catalogs.jsonl
  dataset_series.jsonl
  distributions.jsonl
  data_services.jsonl
  publishers.jsonl
  catalog.db
  catalog.duckdb
  parquet/<table>.parquet
  catalogs/<stable-catalog-id>/datasets.jsonl
  jsonld/<stable-catalog-id>.jsonld
  source_records.jsonl.gz
  raw/dataportal.rdf.gz
  manifest.json
```

The listing is the default enabled product set, with JSON-LD configurable. `source_records.jsonl.gz` is the format-independent current state needed to retain raw source variants and reuse unchanged records even when SQL exports are disabled. It is not a second cache database. The original Dataportal RDF dump is retained compressed to preserve source material outside extracted object graphs. Stale records from a previous successful fetch retain their own RDF and timestamps; a newer dump must not be presented as their origin.

The manifest records schema version, current attempt/outcome, last successful source/catalog refreshes, counts, actual filenames, checksums, compressed sizes, and a compact list of failed/skipped records. It replaces the previous manifest; detailed logs belong in Actions. Do not add daily folders, backups, or accumulated rejection logs.

Write deterministic compact UTF-8 JSONL, sort records and unordered collections consistently, and use deterministic gzip headers. Do not update an unchanged record's conversion timestamp every day. Rebuild product files from one accepted state; retain byte-identical prior binary files when their logical contents and schema have not changed.

Apply gzip only where necessary to stay under the ordinary GitHub file-size limit, using a safety margin (initially 95 MiB). Record `.gz` paths in the manifest and remove obsolete uncompressed/compressed counterparts in the same update. If the combined dataset JSONL still exceeds the limit after gzip, omit only that product and report the omission; preserve the split files. If another required product still exceeds the limit, stop publication and preserve the previous products rather than silently dropping it or pushing an oversized blob. Measure actual sizes during acceptance.

### Configuration and execution

Root `config.toml` owns the operational settings. Proposed initial settings:

```toml
[schedule]
enabled = true
time = "06:00"
timezone = "Europe/Stockholm"

[sources]
dataportal = true
dataeu = true
countries = ["se"]
exclude_eu_catalogs = ["oppnadata"]

[harvest]
concurrency = 8
request_timeout_seconds = 60
download_timeout_seconds = 600
max_attempts = 5

[logging]
level = "INFO"
http_level = "WARNING"

[output]
directory = "data"
combined_jsonl = true
entity_jsonl = true
catalog_jsonl = true
sqlite = true
duckdb = true
parquet = true
jsonld = true
gzip_threshold_mib = 95
```

Validate configuration strictly before network access. Keep the complete TOML contract explicit, adding endpoint overrides, catalog inclusion filters, or timeout settings only where an actual implemented feature uses them. All paths resolve relative to the repo, independent of the caller's working directory. Keep credentials out of TOML; the workflow uses its GitHub token for publishing.

The hourly workflow checks whether the local calendar day is due after the configured time. It performs no harvest dependency installation or API traffic when not due. Use a standard-library-only schedule entry point so this check remains lightweight. Record the daily attempted run, separately from successful data refresh, so a partial outcome does not cause an accidental full harvest every hour. A manual workflow dispatch can bypass the time gate and optionally force refetching. Delayed scheduled invocations can catch up later that day; this is not an exact-time SLA.

### Failure and refresh rules

| Condition | Behavior |
| --- | --- |
| Timeout, connection failure, 429, transient 5xx, unusable partial response | Bounded retries with backoff, jitter, and bounded `Retry-After` handling; isolate the affected request after exhaustion |
| Missing optional metadata or an invalid optional typed value | Preserve source value, emit a warning, keep the record |
| Known record fetch/conversion fails | Retain its last good payload and original successful timestamps, mark stale, continue other records |
| New record cannot be parsed or identified | Preserve identifiable raw evidence where available, report it, omit the unusable structured record, continue |
| Source listing/page/count validation fails | Treat the affected scope as incomplete, preserve previous memberships and records, suppress deletions in that scope |
| Catalog metadata lookup fails | Keep known catalog metadata and process independently usable records; do not fail every member |
| National dump download/XML parse fails | Retain previous Dataportal state; continue the independent EU source |
| Confirmed absence from a complete authoritative scope | Remove that source membership/variant; delete the canonical object only when no surviving reference requires it |
| No usable records from a first-ever run | Fail visibly; do not publish an apparently valid empty catalog |
| All enabled sources fail with previous state available | Preserve previous data and record an unsuccessful refresh; do not call it a fresh successful harvest |
| Configuration, schema integrity, serializer, disk, or publication failure | Fail visibly and preserve the last published product set |

Discovery needs completeness evidence, including pagination termination and available counts. Reconcile EU search and repository listings; source disagreements are not automatic deletion evidence. Include repository-only datasets and preserve the existing distribution-enrichment behavior. Separate optional missing distribution details from failure of the dataset itself.

Do not silently accept partial source listing as complete. Do not label cached RDF with a newly observed modification timestamp when fetching the new content failed. Never replace original URI identity with a lossy generated slug. Preserve DatasetSeries in discovery and catalog membership resolution.

Use a candidate working directory on the runner, validate the complete enabled product set, then publish the accepted set in one Git commit. Temporary working files are not historical exports and are removed when the run ends. Use a single Actions concurrency group with `cancel-in-progress: false`. If a newer repository commit makes the output push unsafe, preserve remote history and abort or retry from a fresh checkout; never force-push over code or user work.

Expected isolated upstream record failures yield a completed-with-warnings report when valid products can be published. Unexpected implementation exceptions must remain visible and must not be swallowed by broad catch-all success handling.

### Implementation sequence: six test-first phases

Each phase consists of small vertical slices: write one failing behavioral test, implement the minimum behavior, then move to the next slice. Do not write the entire test suite before any implementation. Agreed test surfaces are configuration, source discovery/fetching, conversion/identity, safe refresh, and consumer-visible exports. Tests query actual exported databases and read actual Parquet/JSONL files because those files are public products.

1. **Establish the repo-local runner and contract.** Add dependency-only uv configuration, strict TOML loading, the module entry point, initial six models, and a small representative RDF fixture. Start with a failing config test and then a minimal fixture-to-dataset JSONL test. Define stable IDs, language/list shapes, schema version, and namespaced extras. Exit: the fixture runs through the real command into a scratch output directory without installing this repo as a package.

2. **Implement source discovery and retrieval.** Split the reusable HTTP behavior from Dataportal dump/context discovery and EU search/repository retrieval. First test pagination, repository-only records, referenced distribution completion, retries, malformed responses, multiple memberships, series, and missing context metadata. Verify source endpoint assumptions with bounded live calls; retain captured fixtures with provenance. Exit: both sources produce the same source-record contract, with explicit completeness information and no deletion side effects.

3. **Implement preservation, normalization, and incremental refresh.** First test exact-URI identity, preservation of two conflicting source variants, unknown RDF properties, typed literals, all languages, blank-node cycles/topology, and records shared by catalogs. Replace depth-limited extraction with visited-node traversal that preserves complete owned descriptions and explicit cross-entity references, while retaining the entire original dump. Test failed fetches, malformed records, confirmed removal, incomplete/empty enumeration, and reuse with unchanged content. Exit: repeated fixture runs converge, one bad record stays isolated, and no valid old content is relabeled as newly fetched.

4. **Implement the export products.** First test consumer queries/reads for each output, then implement SQLite, DuckDB/Parquet, object and catalog JSONL, and JSON-LD. Use the same accepted entity/link rows for all formats. Check relational integrity, source provenance, multilingual content, cross-format equality, and JSON-LD graph equivalence to the intended RDF graph. Add size/gzip handling, removed-output cleanup, deterministic serialization, and candidate-set validation. Exit: every enabled product represents the same accepted state and no export failure changes published data.

5. **Implement unattended GitHub execution.** First test schedule gating with a fixed clock, timezones/DST, disabled scheduling, delayed runs, once-per-day attempts, manual bypass, and partial outcomes. Add checks and harvest workflows, locked dependency installation, bounded job duration, concurrency control, concise job summaries, current manifest updates, safe commit/push, and data-only path filtering. Exercise successful publication and a rejected/conflicting push in a test repository or controlled branch. Exit: one full manual GitHub-hosted run publishes actual products and the next ordinary due check correctly skips.

6. **Complete live acceptance and public launch.** Perform a full live GitHub Actions harvest, inspect every output format, compare source coverage and accepted/stale/skipped counts, validate database links, read every Parquet file, parse JSONL/JSON-LD, and measure actual sizes and runner resource use. Test a repeat run for reuse and stable output, with failure isolation already proven deterministically. Document commands, TOML settings, schema, sample SQL, downloads/decompression, freshness meanings, and recovery in README. Resolve failures before marking ready, commit/push the completed code and current exports, then change the repository to public and verify anonymous access. The requested target date is 2026-10-17; do not claim completion if verification is unfinished.

### Acceptance criteria

- Both sources are exercised live; mock/fixture evidence is labeled separately from live evidence.
- Underlying catalogs and all known memberships survive; no title-based identities or unproven fuzzy merges.
- All six entity types, original RDF, source variants, unknown properties, and languages survive the documented transformations.
- SQLite, DuckDB, and Parquet have matching logical tables; all files open and relationships validate.
- Combined, entity, and catalog JSONL plus optional JSON-LD are available, subject only to the explicitly agreed combined-file size exception.
- Unchanged records are reused; malformed records, partial listings, and one unavailable source cannot cause cascading deletion or loss of unrelated records.
- Successful products are committed together; failed export/publish attempts leave the previous published products usable.
- Root TOML controls formats, logging, HTTP behavior, and daily timing; GitHub Actions is the sole unattended execution environment.
- Every committed file is within GitHub limits; actual compression/size results are recorded rather than assumed.
- The repo is made public only after implementation and acceptance, and anonymous retrieval is verified.

### Reference checks

- [Provided source gist](https://gist.github.com/rubenselander/5579f0db73753e2bbde85b0df8ee6261); local supplied copy read in full on 2026-10-17.
- [uv non-package projects](https://docs.astral.sh/uv/concepts/projects/config/#project-packaging).
- [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/).
- [RDFLib graph comparison](https://rdflib.readthedocs.io/en/stable/apidocs/rdflib.compare/).
- [GitHub file limits](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
- [GitHub schedule syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).

### Comments

- 2026-10-17: Interview decisions incorporated into this draft. Implementation has not started. Awaiting confirmation that the written plan captures the agreed scope.
- 2026-10-17: Plan approved and implementation completed; 17 behavioral tests and Ruff pass. Full hosted acceptance is in progress. The user explicitly authorized making the repository public before hosted verification after private Actions was blocked by account billing. The repository is public; this supersedes the original visibility ordering above. Performance diagnosis found repeated independent RDF blank-node structures causing global canonicalization to stall; component canonicalization preserves graph topology and passes the previously slow records. Hosted export verification exposed an oversized SQLite product, which remains a publication blocker until corrected and verified.
