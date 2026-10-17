from collections import defaultdict

from dataportalen_export.schema import json_text


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json_text(row) + "\n")


class JsonlExporter:
    def write(self, directory, entities, links, provenance, settings):
        memberships, distributions, sources = (
            defaultdict(list),
            defaultdict(list),
            defaultdict(list),
        )
        for table, left, right in links:
            if table == "catalog_datasets":
                memberships[right].append(left)
            elif table == "dataset_distributions":
                distributions[left].append(entities["distributions"][right])
        for kind, identifier, record in provenance:
            sources[(kind, identifier)].append(record)
        rows = {}
        for identifier, entity in sorted(entities["datasets"].items()):
            rows[identifier] = {
                **entity,
                "catalog_ids": sorted(memberships[identifier]),
                "distributions": distributions[identifier],
                "source_record_ids": sorted(sources[("datasets", identifier)]),
            }
        if settings.combined_jsonl:
            write_jsonl(directory / "datasets.jsonl", rows.values())
        if settings.entity_jsonl:
            for kind, items in entities.items():
                if kind != "datasets":
                    write_jsonl(
                        directory / f"{kind}.jsonl",
                        (
                            {**entity, "source_record_ids": sources[(kind, identifier)]}
                            for identifier, entity in sorted(items.items())
                        ),
                    )
        if settings.catalog_jsonl:
            by_catalog = defaultdict(list)
            for identifier, catalogs in memberships.items():
                for catalog in catalogs:
                    by_catalog[catalog].append(rows[identifier])
            for catalog in entities["catalogs"]:
                write_jsonl(
                    directory / "catalogs" / catalog / "datasets.jsonl",
                    by_catalog[catalog],
                )
