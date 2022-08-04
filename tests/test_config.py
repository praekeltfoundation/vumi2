import os
from tempfile import NamedTemporaryFile

from vumi2.config import (
    BaseConfig,
    load_config,
    load_config_from_environment,
    load_config_from_file,
)


def test_default_base_config():
    config = BaseConfig.deserialise({})
    assert config.amqp.hostname == "127.0.0.1"
    assert config.amqp.port == 5672
    assert config.amqp.username == "guest"
    assert config.amqp.password == "guest"
    assert config.amqp.vhost == "/"
    assert config.amqp_url == ""
    assert config.worker_concurrency == 20


def test_specified_base_config():
    config = BaseConfig.deserialise(
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
    assert config.amqp.password == "pass"
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
    config_obj = BaseConfig.deserialise(config)
    assert config_obj.amqp.username == "guest"


def test_load_config_from_nonexisting_file():
    assert load_config_from_file(filename="nonexisting") == {}


def test_load_config():
    # Environment config should override file config
    os.environ["WORKER_CONCURRENCY"] = "15"
    with NamedTemporaryFile("w") as f:
        os.environ["VUMI_CONFIG_FILE"] = f.name
        f.write(
            """
            amqp:
                port: 1234
            worker_concurrency: 10
            """
        )
        f.flush()
        config = load_config()

    assert config.amqp.port == 1234
    assert config.worker_concurrency == 15
