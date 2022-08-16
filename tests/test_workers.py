import pkg_resources
import pytest
import sentry_sdk

from vumi2.workers import BaseWorker


@pytest.fixture
def config():
    return BaseWorker.CONFIG_CLASS.deserialise({})


async def test_sentry(amqp_connection, config):
    assert sentry_sdk.Hub.current.client is None

    BaseWorker(amqp_connection, config)
    assert sentry_sdk.Hub.current.client is None

    sentry_dsn = "http://key@example.org/0"
    config.sentry_dsn = sentry_dsn
    BaseWorker(amqp_connection, config)
    client = sentry_sdk.Hub.current.client
    assert client is not None
    assert client.dsn == sentry_dsn
    version = pkg_resources.get_distribution("vumi2").version
    assert client.options["release"] == version