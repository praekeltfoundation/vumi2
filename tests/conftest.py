import pytest

from .helpers import (
    ConnectorFactory,
    WorkerFactory,
    aclose_with_timeout,
    amqp_with_cleanup,
)


@pytest.fixture()
async def amqp_connection(monkeypatch):
    async with amqp_with_cleanup(monkeypatch) as amqp:
        yield amqp


@pytest.fixture()
def worker_factory(request, nursery, amqp_connection):
    return WorkerFactory(request, nursery, amqp_connection)


@pytest.fixture()
async def connector_factory(nursery, amqp_connection):
    cf = ConnectorFactory(nursery, amqp_connection)
    async with aclose_with_timeout(cf):
        yield cf


def pytest_configure(config):
    for marker in [
        "worker_class(BaseWorker): use a custom worker class for this test",
        "worker_config(dict): use a custom worker config for this test",
        "worker_cleanup_timeout(float): worker cleanup timeout in seconds",
    ]:
        config.addinivalue_line("markers", marker)
