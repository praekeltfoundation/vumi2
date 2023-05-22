import os
from argparse import Namespace
from pathlib import Path
from tempfile import NamedTemporaryFile

from vumi2.config import (
    BaseConfig,
    load_config,
    load_config_from_cli,
    load_config_from_environment,
    load_config_from_file,
    structure,
)


def deserialise(config: dict) -> BaseConfig:
    return structure(config, BaseConfig)


def test_default_base_config():
    config = deserialise({})
    assert config.amqp.hostname == "127.0.0.1"
    assert config.amqp.port == 5672
    assert config.amqp.username == "guest"  # noqa: S105 (These are fake creds.)
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
    assert config.amqp.username == "user"  # noqa: S105 (These are fake creds.)
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


def test_load_config_from_nonexisting_file():
    assert load_config_from_file(path=Path("nonexisting")) == {}


def test_load_config_from_cli():
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


def test_load_config():
    # CLI config should override environment config
    cli = Namespace()
    cli.worker_concurrency = "5"
    # Environment config should override file config
    os.environ["WORKER_CONCURRENCY"] = "15"
    os.environ["AMQP_HOSTNAME"] = "localhost"
    with NamedTemporaryFile("w") as f:
        os.environ["VUMI_CONFIG_FILE"] = f.name
        f.write("""
            amqp:
                hostname: overwritten
                port: 1234
            worker_concurrency: 10
            """)
        f.flush()
        config = load_config(cli=cli)

    assert config.amqp.port == 1234
    assert config.amqp.hostname == "localhost"
    assert config.worker_concurrency == 5
