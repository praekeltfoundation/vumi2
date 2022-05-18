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
            await self.setup_outbound_consumer(name, self.handle_outbound_message)
            await self.setup_inbound_publisher(name)

        for name in self.config["transport_names"]:
            await self.setup_inbound_consumer(name, self.handle_inbound_message)
            await self.setup_event_consumer(name, self.handle_event)
            await self.setup_outbound_publisher(name)

    # TODO: Teardown

    async def handle_inbound_message(self, message: Message):
        for name, pattern in self.mappings:
            if pattern.match(message.to_addr):
                await self.publish_inbound_message(name=name, message=message)

    async def handle_event(self, _: Event):
        # Explicitly ignore events
        return

    async def handle_outbound_message(self, message: Message):
        await self.publish_outbound_message(
            name=message.transport_name, message=message
        )
