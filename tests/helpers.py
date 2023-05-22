from contextlib import asynccontextmanager
from typing import TypeVar
from warnings import warn

import pytest
from async_amqp import AmqpProtocol  # type: ignore
from async_amqp.exceptions import ChannelClosed  # type: ignore
from attrs import define, field
from trio import MemoryReceiveChannel, Nursery, fail_after, open_memory_channel
from trio.abc import AsyncResource

from vumi2.amqp import create_amqp_client
from vumi2.config import load_config, structure_config
from vumi2.connectors import (
    ConnectorCollection,
    Consumer,
    ReceiveInboundConnector,
    ReceiveOutboundConnector,
)
from vumi2.messages import Event, Message
from vumi2.workers import BaseWorker


async def delete_amqp_queues(amqp: AmqpProtocol, queues: set[str]) -> None:
    """
    Delete all the provided queues, emitting warnings if they're in use
    or not empty.
    """
    for queue in queues:
        try:
            async with amqp.new_channel() as channel:
                await channel.queue_delete(queue, if_empty=True, if_unused=True)
        except ChannelClosed as e:
            warn(e.message, stacklevel=2)
            async with amqp.new_channel() as channel:
                await channel.queue_delete(queue)


@asynccontextmanager
async def amqp_with_cleanup(monkeypatch: pytest.MonkeyPatch):
    """
    Create an amqp client that cleans up after itself.
    """
    queues = set()
    _orig_start_consumer = Consumer.start

    async def _start_consumer(self) -> None:
        queues.add(self.queue_name)
        await _orig_start_consumer(self)

    monkeypatch.setattr(Consumer, "start", _start_consumer)
    config = load_config()
    async with create_amqp_client(config) as amqp:
        try:
            yield amqp
        finally:
            await delete_amqp_queues(amqp, queues)
            # NOTE: At the time of writing, async_amqp (v0.5.3, with no
            # apparent public repo or issue tracker) doesn't properly wait for
            # the connection close handshake before closing its TCP connection.
            # As a result, we randomly see "client unexpectedly closed TCP
            # connection" in rabbitmq logs. There's not much we can do about
            # that, but it doesn't affect the queue and channel cleanup we do
            # here and in the connector code.


@asynccontextmanager
async def aclose_with_timeout(resource: AsyncResource, timeout=1):
    """
    Context manager to close an AsyncResource with a timeout on just the
    aclose.
    """
    try:
        yield resource
    finally:
        with fail_after(timeout):
            await resource.aclose()


@define
class RIConn:
    conn: ReceiveInboundConnector
    _recv_in: MemoryReceiveChannel
    _recv_ev: MemoryReceiveChannel

    @classmethod
    async def create(cls, nursery: Nursery, amqp: AmqpProtocol, name: str):
        send_in, recv_in = open_memory_channel[Message](0)
        send_ev, recv_ev = open_memory_channel[Event](0)
        conn = ReceiveInboundConnector(nursery, amqp, name, concurrency=1)
        await conn.setup(send_in.send, send_ev.send)
        return cls(conn, recv_in, recv_ev)

    async def consume_inbound(self) -> Message:
        return await self._recv_in.receive()

    async def consume_event(self) -> Event:
        return await self._recv_ev.receive()

    async def publish_outbound(self, message: Message):
        return await self.conn.publish_outbound(message)


@define
class ROConn:
    conn: ReceiveOutboundConnector
    _recv_out: MemoryReceiveChannel

    @classmethod
    async def create(cls, nursery: Nursery, amqp: AmqpProtocol, name: str):
        send_out, recv_out = open_memory_channel[Message](0)
        conn = ReceiveOutboundConnector(nursery, amqp, name, concurrency=1)
        await conn.setup(send_out.send)
        return cls(conn, recv_out)

    async def publish_inbound(self, message: Message):
        return await self.conn.publish_inbound(message)

    async def publish_event(self, event: Event):
        return await self.conn.publish_event(event)

    async def consume_outbound(self) -> Message:
        return await self._recv_out.receive()


@define
class ConnectorFactory(AsyncResource):
    nursery: Nursery
    amqp: AmqpProtocol
    _connectors: ConnectorCollection = field(factory=ConnectorCollection)

    async def aclose(self):
        await self._connectors.aclose()

    async def setup_ri(self, name: str) -> RIConn:
        conn = await RIConn.create(self.nursery, self.amqp, name)
        self._connectors.add(conn.conn)
        return conn

    async def setup_ro(self, name: str) -> ROConn:
        conn = await ROConn.create(self.nursery, self.amqp, name)
        self._connectors.add(conn.conn)
        return conn


T = TypeVar("T")


def from_marker(request: pytest.FixtureRequest, mark_name: str, default: T) -> T:
    marker = request.node.get_closest_marker(mark_name)
    if marker is not None:
        return marker.args[0]
    return default


@define
class WorkerFactory:
    """
    Factory for test workers. Returned by the `worker_factory` pytest fixture.
    """

    request: pytest.FixtureRequest
    nursery: Nursery
    amqp: AmqpProtocol

    def __call__(self, default_class: type[BaseWorker], default_config: dict):
        worker_class = from_marker(self.request, "worker_class", default_class)
        config_dict = from_marker(self.request, "worker_config", default_config)
        config = structure_config(config_dict, worker_class)
        return worker_class(self.nursery, self.amqp, config)

    @asynccontextmanager
    async def with_cleanup(self, default_class: type[BaseWorker], default_config: dict):
        timeout = from_marker(self.request, "worker_cleanup_timeout", 1)
        worker = self(default_class, default_config)
        async with aclose_with_timeout(worker, timeout=timeout) as worker:
            yield worker
