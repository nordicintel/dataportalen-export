from pathlib import Path

import pytest
from pydantic import ValidationError

from dataportalen_export.config import load_config


def test_config_defaults_and_paths_are_relative_to_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[output]\ndirectory = "products"\n')
    config = load_config(path)
    assert config.output.directory == tmp_path / "products"
    assert config.harvest.concurrency == 8
    assert config.sources.countries == ["se"]
    assert config.schedule.timezone == "Europe/Stockholm"


@pytest.mark.parametrize(
    "body",
    [
        "[harvest]\nconcurrency = 0",
        "[output]\nsqltie = true",
        '[schedule]\ntimezone = "Invalid/Zone"',
    ],
)
def test_invalid_config_fails_before_work(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body)
    with pytest.raises(ValidationError):
        load_config(path)


def test_repository_config_is_valid():
    assert load_config(Path("config.toml")).output.directory.name == "data"
