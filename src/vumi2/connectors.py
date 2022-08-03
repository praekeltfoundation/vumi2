import json
from logging import getLogger
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, Type

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


# Type[MessageType] doesn't work as expected, so we define a protocol for when we need
# to accept either a Message or Event and only call the serialisation methods
class Serialisable(Protocol):
    def serialise(self) -> Dict[str, Any]:
        ...

    @classmethod
    def deserialise(cls, data: Dict[str, Any]) -> MessageType:
        ...


class Consumer:
    exchange_name = "vumi"
    exchange_type = "direct"
    durable = True

    def __init__(
        self,
        connection: AmqpProtocol,
        queue_name: str,
        callback: CallbackType,
        message_class: Type[Serialisable],
    ) -> None:
        self.connection = connection
        self.queue_name = queue_name
        self.callback = callback
        self.message_class = message_class

    async def start(self) -> None:
        channel = await self.connection.channel()
        await channel.exchange_declare(
            exchange_name=self.exchange_name,
            type_name=self.exchange_type,
            durable=self.durable,
        )
        await channel.queue_declare(self.queue_name, durable=self.durable)
        await channel.queue_bind(self.queue_name, self.exchange_name, self.queue_name)
        await channel.basic_consume(self.consume_message, queue_name=self.queue_name)

    async def consume_message(
        self, channel: Channel, body: bytes, envelope: Envelope, _: Properties
    ) -> None:
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

    def __init__(self, amqp_connection: AmqpProtocol, connector_name: str) -> None:
        self.connection = amqp_connection
        self.name = connector_name
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
            connection=self.connection,
            queue_name=routing_key,
            callback=handler,
            message_class=message_class,
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
    async def setup(
        self, inbound_handler: MessageCallbackType, event_handler: EventCallbackType
    ):
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
