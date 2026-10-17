# Database audit — 2026-10-17

Scope: all data.europa.eu records tagged country `se`, with Swedish/English language maps retained. No catalog exclusions.

## Results

- 25,263 datasets: oppnadata 23,673; geodata-se 1,395; nsip-se 195.
- Five SQLite tables: 3 catalogs, 375 publishers, 25,263 datasets, 37,716 distributions, 7,377 parent-specific service rows.
- 20 JSONL lines selected without replacement using Python SystemRandom, before inspecting their contents. Their 25 distributions, 10 services, catalog and publisher rows were compared to the source values. 1,449 comparisons including integrity/JSON checks passed, with zero mismatches.
- Separately compared every dataset's complete record, all nested distribution/service records, child counts and service parent pointers across the full export. No differences.
- All 47 JSON columns contain valid JSON; SQLite integrity and foreign-key checks passed.
- All three catalog objects match their occurrences in the JSONL. Geographic references, legislation, series, topic categories, reference systems, source dates and quality scores also checked across the whole export.

## Extraction improvements made

Previously useful fields were available only inside the dataset's full source record. They now have direct columns:

- Datasets: country, type, spatial_resources, applicable_legislation, in_series, topic_categories, reference_systems. is_referenced_by now participates in links.
- Catalogs: languages, complete publisher object, complete record_json.
- Distributions: spatial_resolution_m and complete record_json, including full format/license/rights objects.
- Services: all contact_points, actual serves_datasets references, and complete license metadata, alongside existing scalar conveniences.

No tables added. Scalar code/reference columns are deliberately convenient projections; full source objects remain accessible in record_json. Each dataset retains its own publisher description even if a shared publisher URI has differing source descriptions. This is metadata completeness relative to the filtered search JSONL, not a claim that the search API exposes every underlying RDF statement.

## Random sample

Line numbers refer to the locally audited full harvest. IDs are stable lookup keys if later harvest ordering changes.

| JSONL line | Dataset ID | Distributions | Services |
| --- | --- | ---: | ---: |
| 6845 | `https-data-arbetsformedlingen-se-taxonomy-version-latest-query-substitutability-relations-between-occupations-substitutability-relations-between-occupations-json-about0` | 1 | 1 |
| 7826 | `http-api-kolada-se-v3-kpi-u01667` | 1 | 1 |
| 24261 | `https-statistikdatabasen-scb-se-dataset-tab3905` | 1 | 0 |
| 24444 | `https-statistikdatabasen-scb-se-dataset-tab2106` | 1 | 0 |
| 14148 | `http-api-kolada-se-v3-kpi-n60036` | 1 | 1 |
| 3164 | `https-resources-stockholm-se-data-acf1548e-2323-4ba3-a1c3-01311e95376d` | 4 | 0 |
| 7104 | `https-statistikdatabasen-scb-se-dataset-tab4935` | 2 | 0 |
| 7860 | `https-doi-org-10-5878-001827` | 1 | 0 |
| 19188 | `https-doi-org-10-5878-001874` | 1 | 0 |
| 9874 | `https-doi-org-10-23695-d79w-qa67` | 1 | 0 |
| 21623 | `http-api-kolada-se-v3-kpi-u79375` | 1 | 1 |
| 22374 | `http-api-kolada-se-v3-kpi-n01011` | 1 | 1 |
| 1613 | `https-resources-stockholm-se-data-db24d706-6856-4e5a-bffc-b2b67ac948c5` | 2 | 0 |
| 18483 | `http-api-kolada-se-v3-kpi-n02935` | 1 | 1 |
| 19533 | `https-doi-org-10-5878-002254` | 1 | 0 |
| 18277 | `http-api-kolada-se-v3-kpi-n45930` | 1 | 1 |
| 3755 | `http-api-kolada-se-v3-kpi-u07413` | 1 | 1 |
| 19113 | `https-doi-org-10-23695-xg8r-sb12` | 1 | 0 |
| 16796 | `http-api-kolada-se-v3-kpi-n85076` | 1 | 1 |
| 16852 | `http-api-kolada-se-v3-kpi-n02305` | 1 | 1 |

## Gzip publication

The rebuilt SQLite database compresses to 33,487,655 bytes with gzip level 6 (33.49 MB), below the requested 90 MB ceiling. The same random-sample comparisons, full-record/child comparisons and SQLite integrity checks passed after decompression. Output is now `data/catalog.db.gz`; the Zstandard artifact and dependency were removed.
