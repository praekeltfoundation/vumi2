import re
from logging import getLogger
from re import Pattern
from typing import Dict, List, Tuple, Type

import trio
from async_amqp.protocol import AmqpProtocol
from attrs import Factory, define

from vumi2.cli import class_from_string
from vumi2.config import BaseConfig
from vumi2.message_stores import MessageStore, MessageStoreConfig
from vumi2.messages import Event, Message
from vumi2.workers import BaseWorker

logger = getLogger(__name__)


@define
class ToAddressRouterConfig(BaseConfig):
    transport_names: List[str] = Factory(list)
    to_address_mappings: Dict[str, str] = Factory(dict)
    message_store_class: str = "vumi2.message_stores.MemoryMessageStore"
    message_store_config: dict = Factory(dict)


class ToAddressRouter(BaseWorker):
    CONFIG_CLASS = ToAddressRouterConfig

    def __init__(
        self,
        nursery: trio.Nursery,
        amqp_connection: AmqpProtocol,
        config: ToAddressRouterConfig,
    ):
        super().__init__(nursery, amqp_connection, config)
        message_store_cls: Type[MessageStore] = class_from_string(
            config.message_store_class
        )
        message_store_config: MessageStoreConfig = (
            message_store_cls.CONFIG_CLASS.deserialise(config.message_store_config)
        )
        self.message_store: MessageStore = message_store_cls(message_store_config)

    async def setup(self):
        self.mappings: List[Tuple[str, Pattern]] = []

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
        for name, pattern in self.mappings:
            if pattern.match(message.to_addr):
                await self.receive_outbound_connectors[name].publish_inbound(message)

    async def handle_event(self, event: Event):
        outbound = await self.message_store.fetch_outbound(event.user_message_id)
        if outbound is None:
            logger.debug("Cannot find outbound for event %s, not routing", event)
            return
        for name, pattern in self.mappings:
            if pattern.match(outbound.from_addr):
                await self.receive_outbound_connectors[name].publish_event(event)

    async def handle_outbound_message(self, message: Message):
        await self.message_store.store_outbound(message)
        await self.receive_inbound_connectors[message.transport_name].publish_outbound(
            message
        )
