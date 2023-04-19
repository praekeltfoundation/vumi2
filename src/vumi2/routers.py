import re
from logging import getLogger
from re import Pattern

import trio
from async_amqp.protocol import AmqpProtocol
from attrs import Factory, define

from vumi2.cli import class_from_string
from vumi2.config import BaseConfig
from vumi2.message_caches import MessageCache
from vumi2.messages import Event, Message
from vumi2.workers import BaseWorker

logger = getLogger(__name__)


@define
class ToAddressRouterConfig(BaseConfig):
    transport_names: list[str] = Factory(list)
    to_address_mappings: dict[str, str] = Factory(dict)
    message_cache_class: str = "vumi2.message_caches.MemoryMessageCache"
    message_cache_config: dict = Factory(dict)


class ToAddressRouter(BaseWorker):
    config: ToAddressRouterConfig

    def __init__(
        self,
        nursery: trio.Nursery,
        amqp_connection: AmqpProtocol,
        config: ToAddressRouterConfig,
    ):
        super().__init__(nursery, amqp_connection, config)
        message_cache_cls: type[MessageCache] = class_from_string(
            config.message_cache_class
        )
        self.message_cache: MessageCache = message_cache_cls(
            config.message_cache_config
        )

    async def setup(self):
        self.mappings: list[tuple[str, Pattern]] = []

        for name, pattern in self.config.to_address_mappings.items():
            self.mappings.append((name, re.compile(pattern)))
            await self.setup_receive_outbound_connector(
                connector_name=name, outbound_handler=self.handle_outbound_message
            )

        for name in self.config.transport_names:
            await self.setup_receive_inbound_connector(
                connector_name=name,
                inbound_handler=self.handle_inbound_message,
                event_handler=self.handle_event,
            )

    # TODO: Teardown

    async def handle_inbound_message(self, message: Message):
        logger.debug("Processing inbound message %s", message)
        for name, pattern in self.mappings:
            if pattern.match(message.to_addr):
                logger.debug("Routing inbound message to %s", name)
                await self.receive_outbound_connectors[name].publish_inbound(message)

    async def handle_event(self, event: Event):
        logger.debug("Processing event %s", event)
        outbound = await self.message_cache.fetch_outbound(event.user_message_id)
        if outbound is None:
            logger.info("Cannot find outbound for event %s, not routing", event)
            return
        for name, pattern in self.mappings:
            if pattern.match(outbound.from_addr):
                logger.debug("Routing event to %s", name)
                await self.receive_outbound_connectors[name].publish_event(event)

    async def handle_outbound_message(self, message: Message):
        logger.debug("Processing outbound message %s", message)
        await self.message_cache.store_outbound(message)
        await self.receive_inbound_connectors[message.transport_name].publish_outbound(
            message
        )
