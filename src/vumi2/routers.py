import re
from re import Pattern
from typing import List, Tuple

from vumi2.messages import Event, Message
from vumi2.workers import BaseWorker


class ToAddressRouter(BaseWorker):
    # TODO: proper config
    config = {
        "transport_names": ["test1", "test2"],
        "to_address_mappings": {
            "app1": r"^1",
            "app2": r"^2",
        },
    }

    async def setup(self):
        self.mappings: List[Tuple[str, Pattern]] = []

        for name, pattern in self.config["to_address_mappings"].items():
            self.mappings.append((name, re.compile(pattern)))
            await self.setup_receive_outbound_connector(
                connector_name=name, outbound_handler=self.handle_outbound_message
            )

        for name in self.config["transport_names"]:
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
