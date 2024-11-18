import os
from argparse import Namespace
from pathlib import Path

import pytest

from vumi2.config import (
    BaseConfig,
    load_config,
    load_config_from_cli,
    load_config_from_environment,
    structure,
)


def deserialise(config: dict) -> BaseConfig:
    return structure(config, BaseConfig)


def test_default_base_config():
    config = deserialise({})
    assert config.amqp.hostname == "127.0.0.1"
    assert config.amqp.port == 5672
    assert config.amqp.username == "guest"
    assert config.amqp.password == "guest"  # noqa: S105 (These are fake creds.)
    assert config.amqp.vhost == "/"
    assert config.amqp_url == ""
    assert config.worker_concurrency == 20


def test_specified_base_config():
    config = deserialise(
        {
            "amqp": {
                "hostname": "localhost",
                "port": 1234,
                "username": "user",
                "password": "pass",
                "vhost": "/vumi",
            },
            "amqp_url": "amqp://user:pass@localhost:1234/vumi",
            "worker_concurrency": 10,
        }
    )
    assert config.amqp.hostname == "localhost"
    assert config.amqp.port == 1234
    assert config.amqp.username == "user"
    assert config.amqp.password == "pass"  # noqa: S105 (These are fake creds.)
    assert config.amqp.vhost == "/vumi"
    assert config.amqp_url == "amqp://user:pass@localhost:1234/vumi"
    assert config.worker_concurrency == 10


def test_load_config_from_environment():
    environment = {
        "VUMI_AMQP_HOSTNAME": "localhost",
        "VUMI_AMQP_PORT": "1234",
        "VUMI_AMQP_PASSWORD": "pass",
        "VUMI_AMQP_VHOST": "/vumi",
        "VUMI_AMQP_URL": "amqp://user:pass@localhost:1234/vumi",
        "VUMI_WORKER_CONCURRENCY": "10",
    }
    config = load_config_from_environment(
        cls=BaseConfig, source=environment, prefix="vumi"
    )

    assert config == {
        "amqp": {
            "hostname": "localhost",
            "port": "1234",
            "password": "pass",
            "vhost": "/vumi",
        },
        "amqp_url": "amqp://user:pass@localhost:1234/vumi",
        "worker_concurrency": "10",
    }
    config_obj = deserialise(config)
    assert config_obj.amqp.username == "guest"


def test_load_config_from_cli():
    # Make sure we don't have a stray config file set in the environment.
    assert "VUMI_CONFIG_FILE" not in os.environ

    cli = Namespace()
    # For CLI, non-specified values are None
    cli.amqp_host = None
    cli.amqp_port = "1234"
    cli.worker_concurrency = "5"

    config = load_config_from_cli(cli)
    assert config == {
        "amqp": {
            "port": "1234",
        },
        "worker_concurrency": "5",
    }

    config_obj = deserialise(config)
    assert config_obj.amqp.port == 1234
    assert config_obj.worker_concurrency == 5


def test_load_config_from_multiple_sources(monkeypatch, tmp_path):
    """
    Configs from multiple sources are overlaid such that CLI args take
    priority, followed by envvars, with the config file only applying for
    fields not specified elsewhere.
    """

    # CLI config should override environment config
    cli = Namespace()
    cli.worker_concurrency = "5"
    # Environment config should override file config
    monkeypatch.setenv("WORKER_CONCURRENCY", "15")
    monkeypatch.setenv("AMQP_HOSTNAME", "localhost")

    config_path = tmp_path / "test-config.yaml"
    config_path.write_text(
        """
        amqp:
            hostname: overwritten
            port: 1234
        worker_concurrency: 10
        """
    )
    monkeypatch.setenv("VUMI_CONFIG_FILE", str(config_path))

    config = load_config(cli=cli)
    assert config.amqp.port == 1234
    assert config.amqp.hostname == "localhost"
    assert config.worker_concurrency == 5


def test_no_config_file():
    """
    If `VUMI_CONFIG_FILE` is unset (or empty), we don't try to load a
    config file.
    """
    # Make sure we don't have a stray config file set in the environment.
    assert "VUMI_CONFIG_FILE" not in os.environ

    # Load a config with no cli args, assuming no other config-related envvars
    # are set. This should give us default values for everything.
    config = load_config(cli=Namespace())
    assert config == BaseConfig()


def test_no_default_config_file(monkeypatch, tmp_path):
    """
    Previously, we looked for "config.yaml" in the current dir if
    `VUMI_CONFIG_FILE` wasn't specified. This is no longer the case.
    """
    # Make sure we don't have a stray config file set in the environment.
    assert "VUMI_CONFIG_FILE" not in os.environ

    # Change to tmp_dir so we know we're running in a place it's safe to write
    # stuff to.
    monkeypatch.chdir(tmp_path)

    # Write a config file with a guaranteed non-default value in it.
    wc = BaseConfig().worker_concurrency + 1
    Path("config.yaml").write_text(f"worker_concurrency: {wc}")

    # Load a config with no cli args, assuming no other config-related envvars
    # are set. This should give us default values for everything unless we're
    # loading "config.yaml".
    config = load_config(cli=Namespace())
    assert config == BaseConfig()


def test_missing_config_file(monkeypatch, tmp_path):
    """
    If a path to a config file is explicitly provided, it's an error for
    the file to not be there.
    """
    # tmp_path was created for this test and is guaranteed to be empty.
    monkeypatch.setenv("VUMI_CONFIG_FILE", str(tmp_path / "404.yaml"))

    with pytest.raises(FileNotFoundError):
        load_config(cli=Namespace())
