import json
from logging import getLogger
from typing import Awaitable, Callable, Dict, Optional, Type

import trio
from async_amqp import AmqpProtocol
from async_amqp.channel import Channel
from async_amqp.envelope import Envelope
from async_amqp.properties import Properties

from vumi2.async_helpers import maybe_awaitable
from vumi2.messages import Event, Message, MessageType

logger = getLogger(__name__)


CallbackType = Callable[[MessageType], Optional[Awaitable[None]]]
MessageCallbackType = Callable[[Message], Optional[Awaitable[None]]]
EventCallbackType = Callable[[Event], Optional[Awaitable[None]]]


class Consumer:
    exchange_name = "vumi"
    exchange_type = "direct"
    durable = True

    def __init__(
        self,
        nursery: trio.Nursery,
        connection: AmqpProtocol,
        queue_name: str,
        callback: CallbackType,
        message_class: Type[MessageType],
        concurrency: int,
    ) -> None:
        self.nursery = nursery
        self.connection = connection
        self.queue_name = queue_name
        self.callback = callback
        self.message_class = message_class
        self.concurrency = concurrency
        self.send_channel, self.receive_channel = trio.open_memory_channel(concurrency)

    async def start(self) -> None:
        channel = await self.connection.channel()
        await channel.basic_qos(prefetch_count=self.concurrency)
        await channel.exchange_declare(
            exchange_name=self.exchange_name,
            type_name=self.exchange_type,
            durable=self.durable,
        )
        await channel.queue_declare(self.queue_name, durable=self.durable)
        await channel.queue_bind(self.queue_name, self.exchange_name, self.queue_name)
        await channel.basic_consume(self.queue_message, queue_name=self.queue_name)
        for _ in range(self.concurrency):
            self.nursery.start_soon(self.consume_message)

    async def queue_message(
        self, channel: Channel, body: bytes, envelope: Envelope, properties: Properties
    ) -> None:
        await self.send_channel.send((channel, body, envelope, properties))

    async def consume_message(self) -> None:
        async for channel, body, envelope, _ in self.receive_channel:
            try:
                msg = self.message_class.deserialise(json.loads(body))
                await maybe_awaitable(self.callback(msg))
                await channel.basic_client_ack(envelope.delivery_tag)
            except Exception as e:
                logger.exception(e)
                await channel.basic_client_nack(
                    delivery_tag=envelope.delivery_tag, requeue=False
                )


class Publisher:
    exchange_name = "vumi"
    exchange_type = "direct"
    durable = True
    delivery_mode = 2

    def __init__(self, connection: AmqpProtocol, routing_key: str) -> None:
        self.connection = connection
        self.routing_key = routing_key

    async def start(self) -> None:
        self.channel = await self.connection.channel()

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
        self._consumers: Dict[str, Consumer] = {}
        self._publishers: Dict[str, Publisher] = {}

    def routing_key(self, message_type: str):
        return f"{self.name}.{message_type}"

    async def _setup_consumer(
        self,
        message_type: str,
        handler: CallbackType,
        message_class=Type[MessageType],
    ) -> None:
        routing_key = self.routing_key(message_type)
        consumer = Consumer(
            nursery=self.nursery,
            connection=self.connection,
            queue_name=routing_key,
            callback=handler,
            message_class=message_class,
            concurrency=self.concurrency,
        )
        await consumer.start()
        self._consumers[message_type] = consumer

    async def _setup_publisher(self, message_type: str) -> None:
        routing_key = self.routing_key(message_type)
        publisher = Publisher(self.connection, routing_key)
        await publisher.start()
        self._publishers[message_type] = publisher

    async def _publish_message(self, message_type: str, message: MessageType) -> None:
        publisher = self._publishers[message_type]
        await publisher.publish_raw(json.dumps(message.serialise()).encode())


class ReceiveInboundConnector(BaseConnector):
    async def setup(self, inbound_handler: CallbackType, event_handler: CallbackType):
        await self._setup_publisher("outbound")
        await self._setup_consumer("inbound", inbound_handler, Message)
        await self._setup_consumer("event", event_handler, Event)

    async def publish_outbound(self, message: Message):
        await self._publish_message("outbound", message)


class ReceiveOutboundConnector(BaseConnector):
    async def setup(self, outbound_handler: CallbackType):
        await self._setup_publisher("inbound")
        await self._setup_publisher("event")
        await self._setup_consumer("outbound", outbound_handler, Message)

    async def publish_inbound(self, message: Message):
        await self._publish_message("inbound", message)

    async def publish_event(self, event: Event):
        await self._publish_message("event", event)
