from vumi2.config import BaseConfig


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
