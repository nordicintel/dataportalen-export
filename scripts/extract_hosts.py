"""Extract sorted unique base domains from the complete gzip JSONL export."""

import gzip
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import tldextract

ROOT = Path(__file__).resolve().parents[1]
URL = re.compile(
    r"""(?:\b[a-z][a-z0-9+.-]*://|(?<![\w:/])//|\bwww\.)[^\s<>"'\\]+""", re.I
)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def extract_hosts(source: Path, target: Path) -> int:
    hosts = set()
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        for line in stream:
            for text in strings(json.loads(line)):
                for match in URL.finditer(text):
                    url = match.group().rstrip(".,;:!?)}")
                    if url.lower().startswith("www."):
                        url = "//" + url
                    try:
                        host = urlsplit(url).hostname
                    except ValueError:
                        continue
                    if host:
                        host = unquote(host).lower().rstrip(".")
                        if re.fullmatch(r"[\w.:-]+", host):
                            hosts.add(host)
    suffixes = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    hosts = {suffixes(host).top_domain_under_public_suffix or host for host in hosts}
    temporary = target.with_suffix(".txt.tmp")
    try:
        temporary.write_text(
            "".join(host + "\n" for host in sorted(hosts)), encoding="utf-8"
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return len(hosts)


def main():
    config = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    if config["hosts"]["enabled"]:
        count = extract_hosts(ROOT / "data/datasets.jsonl.gz", ROOT / "data/hosts.txt")
        print(f"Wrote {count} unique base domains to data/hosts.txt")
    else:
        print("Host extraction disabled; existing hosts.txt left unchanged.")


if __name__ == "__main__":
    main()
