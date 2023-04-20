import pytest

from vumi2.amqp import create_amqp_client
from vumi2.config import load_config
from vumi2.connectors import Consumer

from .helpers import delete_amqp_queues


@pytest.fixture(scope="function")
async def amqp_connection(monkeypatch):
    queues = set()
    _orig_start_consumer = Consumer.start

    async def _start_consumer(self) -> None:
        queues.add(self.queue_name)
        await _orig_start_consumer(self)

    monkeypatch.setattr(Consumer, "start", _start_consumer)
    config = load_config()
    async with create_amqp_client(config) as amqp_connection:
        try:
            yield amqp_connection
        finally:
            await delete_amqp_queues(amqp_connection, queues)
