import json
from collections.abc import Awaitable
from logging import getLogger
from typing import Callable, Optional, overload

import trio
from async_amqp import AmqpProtocol  # type: ignore
from async_amqp.channel import Channel  # type: ignore
from async_amqp.envelope import Envelope  # type: ignore
from async_amqp.properties import Properties  # type: ignore
from trio.abc import AsyncResource

from vumi2.async_helpers import maybe_awaitable
from vumi2.messages import Event, Message, MessageType

logger = getLogger(__name__)


MessageCallbackType = Callable[[Message], Optional[Awaitable[None]]]
EventCallbackType = Callable[[Event], Optional[Awaitable[None]]]
_AmqpChType = tuple[Channel, bytes, Envelope, Properties]


class Consumer(AsyncResource):
    exchange_name = "vumi"
    exchange_type = "direct"
    durable = True

    @overload
    def __init__(
        self,
        nursery: trio.Nursery,
        connection: AmqpProtocol,
        queue_name: str,
        callback: MessageCallbackType,
        message_class: type[Message],
        concurrency: int,
    ) -> None:
        ...

    @overload
    def __init__(
        self,
        nursery: trio.Nursery,
        connection: AmqpProtocol,
        queue_name: str,
        callback: EventCallbackType,
        message_class: type[Event],
        concurrency: int,
    ) -> None:
        ...

    def __init__(
        self, nursery, connection, queue_name, callback, message_class, concurrency
    ) -> None:
        self.nursery = nursery
        self.connection = connection
        self.queue_name = queue_name
        self.callback = callback
        self.message_class = message_class
        self.concurrency = concurrency
        self.send_channel, self.receive_channel = trio.open_memory_channel[_AmqpChType](
            concurrency,
        )
        self._active_consumers = 0
        self._closing = False
        self._closed = trio.Event()

    async def start(self) -> None:
        self.channel = channel = await self.connection.channel()
        await channel.basic_qos(prefetch_count=self.concurrency)
        await channel.exchange_declare(
            exchange_name=self.exchange_name,
            type_name=self.exchange_type,
            durable=self.durable,
        )
        await channel.queue_declare(self.queue_name, durable=self.durable)
        await channel.queue_bind(self.queue_name, self.exchange_name, self.queue_name)
        await channel.basic_consume(self.queue_message, queue_name=self.queue_name)
        async with self.receive_channel:
            for _ in range(self.concurrency):
                self._active_consumers += 1
                receive_channel = self.receive_channel.clone()
                self.nursery.start_soon(self.consume_messages, receive_channel)

    async def aclose(self):
        if not self._closing:
            self._closing = True
            self.send_channel.close()
        await self._closed.wait()
        if self.channel.is_open:
            await self.channel.close()

    async def queue_message(
        self, channel: Channel, body: bytes, envelope: Envelope, properties: Properties
    ) -> None:
        if not self._closing:
            await self.send_channel.send((channel, body, envelope, properties))

    async def consume_message(self, channel, body, envelope) -> None:
        try:
            msg = self.message_class.deserialise(json.loads(body))
            await maybe_awaitable(self.callback(msg))
            await channel.basic_client_ack(envelope.delivery_tag)
        except Exception as e:
            logger.exception(e)
            await channel.basic_client_nack(
                delivery_tag=envelope.delivery_tag, requeue=False
            )

    async def consume_messages(self, receive_channel) -> None:
        try:
            async with receive_channel:
                async for channel, body, envelope, _ in receive_channel:
                    await self.consume_message(channel, body, envelope)
        finally:
            self._active_consumers -= 1
            if self._active_consumers == 0:
                self._closed.set()


class Publisher(AsyncResource):
    exchange_name = "vumi"
    exchange_type = "direct"
    durable = True
    delivery_mode = 2

    def __init__(self, connection: AmqpProtocol, routing_key: str) -> None:
        self.connection = connection
        self.routing_key = routing_key

    async def start(self) -> None:
        self.channel = await self.connection.channel()

    async def aclose(self):
        if self.channel.is_open:
            await self.channel.close()

    async def publish_raw(self, data: bytes) -> None:
        await self.channel.basic_publish(
            payload=data,
            exchange_name=self.exchange_name,
            routing_key=self.routing_key,
            properties={"delivery_mode": self.delivery_mode},
        )


class BaseConnector:
    """
    A connector combines the publishers and consumers for various message types together
    """

    def __init__(
        self,
        nursery: trio.Nursery,
        amqp_connection: AmqpProtocol,
        connector_name: str,
        concurrency: int,
    ) -> None:
        self.nursery = nursery
        self.connection = amqp_connection
        self.name = connector_name
        self.concurrency = concurrency
        self._consumers: dict[str, Consumer] = {}
        self._publishers: dict[str, Publisher] = {}

    def routing_key(self, message_type: str):
        return f"{self.name}.{message_type}"

    @overload
    async def _setup_consumer(
        self,
        message_type: str,
        handler: MessageCallbackType,
        message_class: type[Message],
    ) -> None:
        ...

    @overload
    async def _setup_consumer(
        self, message_type: str, handler: EventCallbackType, message_class: type[Event]
    ) -> None:
        ...

    async def _setup_consumer(self, message_type, handler, message_class) -> None:
        routing_key = self.routing_key(message_type)
        consumer = Consumer(
            nursery=self.nursery,
            connection=self.connection,
            queue_name=routing_key,
            callback=handler,
            message_class=message_class,
            concurrency=self.concurrency,
        )
        self._consumers[message_type] = consumer

    async def start_consuming(self):
        async with trio.open_nursery() as nursery:
            for consumer in self._consumers.values():
                nursery.start_soon(consumer.start)

    async def _setup_publisher(self, message_type: str) -> None:
        routing_key = self.routing_key(message_type)
        publisher = Publisher(self.connection, routing_key)
        await publisher.start()
        self._publishers[message_type] = publisher

    async def _publish_message(self, message_type: str, message: MessageType) -> None:
        publisher = self._publishers[message_type]
        await publisher.publish_raw(json.dumps(message.serialise()).encode())

    async def aclose_consumers(self):
        """
        Cleanly close all consumers.

        To avoid problems with consumers that publish replies or events,
        this should be called before closing publishers on any
        connector.
        """
        async with trio.open_nursery() as nursery:
            for consumer in self._consumers.values():
                nursery.start_soon(consumer.aclose)

    async def aclose_publishers(self):
        """
        Cleanly close all publishers.

        To avoid problems with consumers that publish replies or events,
        this should only be called after closing consumers on all
        connectors.
        """
        async with trio.open_nursery() as nursery:
            for publisher in self._publishers.values():
                nursery.start_soon(publisher.aclose)


class ReceiveInboundConnector(BaseConnector):
    async def setup(
        self, inbound_handler: MessageCallbackType, event_handler: EventCallbackType
    ):
        await self._setup_publisher("outbound")
        await self._setup_consumer("inbound", inbound_handler, Message)
        await self._setup_consumer("event", event_handler, Event)

    async def publish_outbound(self, message: Message):
        await self._publish_message("outbound", message)


class ReceiveOutboundConnector(BaseConnector):
    async def setup(self, outbound_handler: MessageCallbackType):
        await self._setup_publisher("inbound")
        await self._setup_publisher("event")
        await self._setup_consumer("outbound", outbound_handler, Message)

    async def publish_inbound(self, message: Message):
        await self._publish_message("inbound", message)

    async def publish_event(self, event: Event):
        await self._publish_message("event", event)


class ConnectorCollection(AsyncResource):
    """
    A collection of connectors that must all be closed together.
    """

    def __init__(self):
        self.connectors: set[BaseConnector] = set()

    def add(self, connector: BaseConnector) -> None:
        self.connectors.add(connector)

    async def aclose(self):
        async with trio.open_nursery() as nursery:
            for connector in self.connectors:
                nursery.start_soon(connector.aclose_consumers)
        async with trio.open_nursery() as nursery:
            for connector in self.connectors:
                nursery.start_soon(connector.aclose_publishers)

    async def start_consuming(self):
        async with trio.open_nursery() as nursery:
            for connector in self.connectors:
                nursery.start_soon(connector.start_consuming)
