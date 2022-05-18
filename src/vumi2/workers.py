import json
from typing import Awaitable, Callable, Dict, Optional

from async_amqp import AmqpProtocol

from vumi2.messages import Event, Message, MessageType
from vumi2.services import Consumer, Publisher

MESSAGE_CLASSES = {
    "inbound": Message,
    "outbound": Message,
    "event": Event,
}


class BaseWorker:
    def __init__(self, amqp_connection: AmqpProtocol):
        self.connection = amqp_connection
        self._consumers: Dict[str, Consumer] = {}
        self._publishers: Dict[str, Publisher] = {}

    async def setup(self):
        pass

    @staticmethod
    def routing_key(name: str, message_type: str):
        return f"{name}.{message_type}"

    async def setup_publisher(self, name: str, message_type: str) -> None:
        routing_key = self.routing_key(name, message_type)
        publisher = Publisher(self.connection, routing_key)
        await publisher.start()
        self._publishers[routing_key] = publisher

    async def setup_inbound_publisher(self, name: str) -> None:
        await self.setup_publisher(name, "inbound")

    async def setup_outbound_publisher(self, name: str) -> None:
        await self.setup_publisher(name, "outbound")

    async def setup_consumer(
        self,
        name: str,
        message_type: str,
        handler: Callable,
        message_class=MessageType,
    ) -> None:
        routing_key = self.routing_key(name, message_type)
        consumer = Consumer(
            connection=self.connection,
            queue_name=routing_key,
            callback=handler,
            message_class=message_class,
        )
        await consumer.start()
        self._consumers[routing_key] = consumer

    async def setup_inbound_consumer(
        self, name: str, handler: Callable[[Message], Optional[Awaitable[None]]]
    ) -> None:
        await self.setup_consumer(
            name=name, message_type="inbound", handler=handler, message_class=Message
        )

    async def setup_event_consumer(
        self, name: str, handler: Callable[[Event], Optional[Awaitable[None]]]
    ) -> None:
        await self.setup_consumer(
            name=name, message_type="event", handler=handler, message_class=Event
        )

    async def setup_outbound_consumer(
        self, name: str, handler: Callable[[Message], Optional[Awaitable[None]]]
    ) -> None:
        await self.setup_consumer(
            name=name, message_type="outbound", handler=handler, message_class=Message
        )

    async def publish_message(
        self, name: str, message_type: str, message: MessageType
    ) -> None:
        routing_key = self.routing_key(name, message_type)
        publisher = self._publishers[routing_key]
        await publisher.publish_raw(json.dumps(message.serialise()).encode())

    async def publish_inbound_message(self, name: str, message: Message) -> None:
        await self.publish_message(name, "inbound", message)

    async def publish_outbound_message(self, name: str, message: Message) -> None:
        await self.publish_message(name, "outbound", message)
