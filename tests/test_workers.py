import importlib.metadata

import pytest
import sentry_sdk

from vumi2.workers import BaseWorker


class FailingHealthcheckWorker(BaseWorker):
    async def setup(self):
        self.healthchecks["failing"] = self.failing_healthcheck

    async def failing_healthcheck(self):
        return {"health": "down"}


@pytest.fixture
def config():
    return BaseWorker.get_config_class().deserialise({})


async def test_sentry(amqp_connection, config, nursery):
    assert sentry_sdk.Hub.current.client is None

    BaseWorker(nursery, amqp_connection, config)
    assert sentry_sdk.Hub.current.client is None

    try:
        sentry_dsn = "http://key@example.org/0"
        config.sentry_dsn = sentry_dsn
        BaseWorker(nursery, amqp_connection, config)
        client = sentry_sdk.Hub.current.client
        assert client is not None
        assert client.dsn == sentry_dsn
        version = importlib.metadata.distribution("vumi2").version
        assert client.options["release"] == version
    finally:
        # Disable sentry for the rest of the tests
        sentry_sdk.init()


async def test_http_server(amqp_connection, config, nursery):
    config.http_bind = "localhost"
    worker = BaseWorker(nursery, amqp_connection, config)
    assert worker.http_app is not None


async def test_healthcheck(amqp_connection, config, nursery):
    config.http_bind = "localhost"
    worker = BaseWorker(nursery, amqp_connection, config)
    client = worker.http_app.test_client()
    response = await client.get("/health")
    data = await response.json
    assert data["health"] == "ok"
    assert data["components"]["amqp"]["state"] == "open"


async def test_down_healthcheck(amqp_connection, config, nursery):
    config.http_bind = "localhost"
    worker = FailingHealthcheckWorker(nursery, amqp_connection, config)
    await worker.setup()
    client = worker.http_app.test_client()
    response = await client.get("/health")
    data = await response.json
    assert data["health"] == "down"
    assert data["components"]["failing"] == {"health": "down"}
