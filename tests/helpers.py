from contextlib import asynccontextmanager
from typing import TypeVar
from warnings import warn

from async_amqp import AmqpProtocol  # type: ignore
from async_amqp.exceptions import ChannelClosed  # type: ignore
from pytest import FixtureRequest, MonkeyPatch
from trio import Nursery, fail_after
from trio.abc import AsyncResource

from vumi2.amqp import create_amqp_client
from vumi2.config import load_config
from vumi2.connectors import Consumer
from vumi2.workers import BaseWorker


async def delete_amqp_queues(amqp_connection: AmqpProtocol, queues: set[str]) -> None:
    """
    Delete all the provided queues, emitting warnings if they're in use
    or not empty.
    """
    for queue in queues:
        try:
            async with amqp_connection.new_channel() as channel:
                await channel.queue_delete(queue, if_empty=True, if_unused=True)
        except ChannelClosed as e:
            warn(e.message, stacklevel=2)
            async with amqp_connection.new_channel() as channel:
                await channel.queue_delete(queue)


@asynccontextmanager
async def amqp_connection_with_cleanup(monkeypatch: MonkeyPatch):
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
    async with create_amqp_client(config) as amqp_connection:
        try:
            yield amqp_connection
        finally:
            await delete_amqp_queues(amqp_connection, queues)


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


T = TypeVar("T")


def from_marker(request: FixtureRequest, mark_name: str, default: T) -> T:
    marker = request.node.get_closest_marker(mark_name)
    if marker is not None:
        return marker.args[0]
    return default


def worker_with_config(
    request: FixtureRequest,
    nursery: Nursery,
    amqp: AmqpProtocol,
    default_class: type[BaseWorker],
    default_config: dict,
):
    worker_class = from_marker(request, "worker_class", default_class)
    config_dict = from_marker(request, "worker_config", default_config)
    config = worker_class.get_config_class().deserialise(config_dict)
    return worker_class(nursery, amqp, config)


@asynccontextmanager
async def worker_with_cleanup(
    request: FixtureRequest,
    nursery: Nursery,
    amqp: AmqpProtocol,
    default_class: type[BaseWorker],
    default_config: dict,
):
    worker = worker_with_config(request, nursery, amqp, default_class, default_config)
    async with aclose_with_timeout(worker):
        yield worker
