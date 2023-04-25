import pytest

from .helpers import amqp_connection_with_cleanup


@pytest.fixture()
async def amqp_connection(monkeypatch):
    async with amqp_connection_with_cleanup(monkeypatch) as amqp_connection:
        yield amqp_connection


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "worker_config(dict): use a custom worker config for this test"
    )
