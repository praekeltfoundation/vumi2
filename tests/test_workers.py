import importlib.metadata

import pytest
import sentry_sdk

from vumi2.workers import BaseWorker

from .helpers import from_marker


class FailingHealthcheckWorker(BaseWorker):
    async def setup(self):
        self.healthchecks["failing"] = self.failing_healthcheck

    async def failing_healthcheck(self):
        return {"health": "down"}


@pytest.fixture
def config(request):
    cfg_dict = from_marker(request, "worker_config", {"http_bind": "localhost"})
    return BaseWorker.get_config_class().deserialise(cfg_dict)


@pytest.mark.worker_config({})
async def test_sentry_unconfigured(amqp_connection, config, nursery):
    """
    When sentry_dsn isn't configured, sentry isn't set up.
    """
    assert sentry_sdk.Hub.current.client is None
    BaseWorker(nursery, amqp_connection, config)
    assert sentry_sdk.Hub.current.client is None


@pytest.mark.worker_config({"sentry_dsn": "http://key@example.org/0"})
async def test_sentry_configured(amqp_connection, config, nursery):
    """
    When sentry_dsn is configured, sentry is set up at worker creation time.
    """
    assert sentry_sdk.Hub.current.client is None
    try:
        BaseWorker(nursery, amqp_connection, config)
        client = sentry_sdk.Hub.current.client
        assert client is not None
        assert client.dsn == "http://key@example.org/0"
        version = importlib.metadata.distribution("vumi2").version
        assert client.options["release"] == version
    finally:
        # Disable sentry for the rest of the tests
        sentry_sdk.init()


@pytest.mark.worker_config({})
async def test_http_server_unconfigured(amqp_connection, config, nursery):
    """
    When http_bind isn't configured, the worker http server isn't set up.
    """
    worker = BaseWorker(nursery, amqp_connection, config)
    assert not hasattr(worker, "http")


@pytest.mark.worker_config({"http_bind": "localhost"})
async def test_http_server_configured(amqp_connection, config, nursery):
    """
    When http_bind is configured, the worker http server is set up and
    endpoints may be configured.
    """
    worker = BaseWorker(nursery, amqp_connection, config)
    assert worker.http is not None
    worker.http.app.add_url_rule("/hi", view_func=lambda: ("hello", 200))
    response = await worker.http.app.test_client().get("/hi")
    assert await response.data == b"hello"


async def test_healthcheck(amqp_connection, config, nursery):
    worker = BaseWorker(nursery, amqp_connection, config)
    client = worker.http.app.test_client()
    response = await client.get("/health")
    data = await response.json
    assert data["health"] == "ok"
    assert data["components"]["amqp"]["state"] == "open"


async def test_down_healthcheck(amqp_connection, config, nursery):
    worker = FailingHealthcheckWorker(nursery, amqp_connection, config)
    await worker.setup()
    client = worker.http.app.test_client()
    response = await client.get("/health")
    data = await response.json
    assert data["health"] == "down"
    assert data["components"]["failing"] == {"health": "down"}
