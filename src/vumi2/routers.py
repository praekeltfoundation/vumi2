import re
from re import Pattern
from typing import Dict, List, Tuple

from async_amqp.protocol import AmqpProtocol
from attrs import Factory, define

from vumi2.config import BaseConfig
from vumi2.messages import Event, Message
from vumi2.workers import BaseWorker


@define
class ToAddressRouterConfig(BaseConfig):
    transport_names: List[str] = Factory(list)
    to_address_mappings: Dict[str, str] = Factory(dict)


class ToAddressRouter(BaseWorker):
    CONFIG_CLASS = ToAddressRouterConfig

    def __init__(self, amqp_connection: AmqpProtocol, config: ToAddressRouterConfig):
        super().__init__(amqp_connection, config)

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

    async def handle_event(self, _: Event):
        # Explicitly ignore events
        return

    async def handle_outbound_message(self, message: Message):
        await self.receive_inbound_connectors[message.transport_name].publish_outbound(
            message
        )
