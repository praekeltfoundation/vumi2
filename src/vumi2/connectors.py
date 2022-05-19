import json
from logging import getLogger
from typing import Dict, Type

from async_amqp import AmqpProtocol

from vumi2.messages import Event, Message, MessageType
from vumi2.services import CallbackType, Consumer, Publisher

logger = getLogger(__name__)


class BaseConnector:
    """
    A connector combines the publishers and consumers for various message types together
    """

    def __init__(self, amqp_connection: AmqpProtocol, connector_name: str) -> None:
        self.connection = amqp_connection
        self.name = connector_name
        self._consumers: Dict[str, Consumer] = {}
        self._publishers: Dict[str, Publisher] = {}

    async def setup(self) -> None:
        return

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
    async def setup(self):
        await self._setup_publisher("outbound")
        await self._setup_consumer("inbound", self._default_inbound_handler, Message)
        await self._setup_consumer("event", self._default_event_handler, Event)

    async def _default_inbound_handler(self, msg: Message):
        logger.warning(f"No inbound handler for {self.name}: {msg}")

    async def _default_event_handler(self, event: Event):
        logger.warning(f"No event handler for {self.name}: {event}")

    def set_inbound_handler(self, handler: CallbackType):
        self._consumers["inbound"].callback = handler

    def set_event_handler(self, handler: CallbackType):
        self._consumers["event"].callback = handler

    async def publish_outbound(self, message: Message):
        await self._publish_message("outbound", message)


class ReceiveOutboundConnector(BaseConnector):
    async def setup(self):
        await self._setup_publisher("inbound")
        await self._setup_publisher("event")
        await self._setup_consumer("outbound", self._default_outbound_handler, Message)

    async def _default_outbound_handler(self, msg: Message):
        logger.warning(f"No outbound handler for {self.name}: {msg}")

    def set_outbound_handler(self, handler: CallbackType):
        self._consumers["outbound"].callback = handler

    async def publish_inbound(self, message: Message):
        await self._publish_message("inbound", message)

    async def publish_event(self, event: Event):
        await self._publish_message("event", event)
