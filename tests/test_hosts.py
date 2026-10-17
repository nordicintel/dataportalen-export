import gzip
import json

from scripts.extract_hosts import extract_hosts


def test_hosts_include_nested_keys_and_embedded_urls(tmp_path):
    source = tmp_path / "datasets.jsonl.gz"
    target = tmp_path / "hosts.txt"
    target.write_text("old.example\n")
    record = {
        "https://Vocabulary.EXAMPLE/type": [
            {"url": "HTTPS://user:password@API.Example.org:8443/path?q=yes#part"},
            '<a href="https://api.example.org/other">link</a> (https://other.example/path).',
            "ftp://files.example/archive",
            "https://files%2eexample/archive",
            "https://bad,host/path",
            "https://www.ehalsomyndigheten.se/info",
            "https://api.ehalsomyndigheten.se/",
            "https://maps.example.co.uk/path",
            "https://www.example.co.uk/",
            "//cdn.example/file",
            "mailto:person@example.org",
            "https://[broken",
        ],
        "unfamiliar": {"deep": ["https://sub.example.org/", None, 17]},
    }
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    assert extract_hosts(source, target) == 7
    assert target.read_text().splitlines() == [
        "cdn.example",
        "ehalsomyndigheten.se",
        "example.co.uk",
        "example.org",
        "files.example",
        "other.example",
        "vocabulary.example",
    ]


def test_disabled_extraction_leaves_existing_file(tmp_path, monkeypatch):
    from scripts import extract_hosts as module

    (tmp_path / "config.toml").write_text("[hosts]\nenabled = false\n")
    target = tmp_path / "data" / "hosts.txt"
    target.parent.mkdir()
    target.write_text("old.example\n")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    module.main()
    assert target.read_text() == "old.example\n"
