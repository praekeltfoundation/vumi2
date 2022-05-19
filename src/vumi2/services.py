import json
from logging import getLogger
from typing import Awaitable, Callable, Optional, Type

from async_amqp import AmqpProtocol
from async_amqp.channel import Channel
from async_amqp.envelope import Envelope
from async_amqp.properties import Properties

from vumi2.async_helpers import maybe_awaitable
from vumi2.messages import MessageType

logger = getLogger(__name__)

CallbackType = Callable[[MessageType], Optional[Awaitable[None]]]


class Consumer:
    exchange_name = "vumi"
    exchange_type = "direct"
    durable = True

    def __init__(
        self,
        connection: AmqpProtocol,
        queue_name: str,
        callback: CallbackType,
        message_class: Type[MessageType],
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
