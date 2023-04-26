import importlib.metadata

import pytest
import sentry_sdk

from vumi2.workers import BaseWorker


class FailingHealthcheckWorker(BaseWorker):
    async def setup(self):
        self.healthchecks["failing"] = self.failing_healthcheck

    async def failing_healthcheck(self):
        return {"health": "down"}


@pytest.fixture()
async def worker(worker_factory):
    config = {"http_bind": "localhost"}
    async with worker_factory.with_cleanup(BaseWorker, config) as worker:
        yield worker


async def test_sentry_unconfigured(worker_factory):
    """
    When sentry_dsn isn't configured, sentry isn't set up.
    """
    assert sentry_sdk.Hub.current.client is None
    worker_factory(BaseWorker, {})
    assert sentry_sdk.Hub.current.client is None


async def test_sentry_configured(worker_factory):
    """
    When sentry_dsn is configured, sentry is set up at worker creation time.
    """
    assert sentry_sdk.Hub.current.client is None
    try:
        worker_factory(BaseWorker, {"sentry_dsn": "http://key@example.org/0"})
        client = sentry_sdk.Hub.current.client
        assert client is not None
        assert client.dsn == "http://key@example.org/0"
        version = importlib.metadata.distribution("vumi2").version
        assert client.options["release"] == version
    finally:
        # Disable sentry for the rest of the tests
        sentry_sdk.init()


async def test_http_server_unconfigured(worker_factory):
    """
    When http_bind isn't configured, the worker http server isn't set up.
    """
    worker = worker_factory(BaseWorker, {})
    assert not hasattr(worker, "http")


async def test_http_server_configured(worker_factory):
    """
    When http_bind is configured, the worker http server is set up and
    endpoints may be configured.
    """
    worker = worker_factory(BaseWorker, {"http_bind": "localhost"})
    assert worker.http is not None
    worker.http.app.add_url_rule("/hi", view_func=lambda: ("hello", 200))
    response = await worker.http.app.test_client().get("/hi")
    assert await response.data == b"hello"


async def test_healthcheck(worker):
    client = worker.http.app.test_client()
    response = await client.get("/health")
    data = await response.json
    assert data["health"] == "ok"
    assert data["components"]["amqp"]["state"] == "open"


# The .with_args is to bypass pytest's special behaviour for callable marker args.
@pytest.mark.worker_class.with_args(FailingHealthcheckWorker)
async def test_down_healthcheck(worker):
    await worker.setup()
    client = worker.http.app.test_client()
    response = await client.get("/health")
    data = await response.json
    assert data["health"] == "down"
    assert data["components"]["failing"] == {"health": "down"}
