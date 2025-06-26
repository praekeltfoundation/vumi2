from attrs import define

from vumi2.messages import Event, Message, Session
from vumi2.workers import BaseConfig, BaseWorker


@define(kw_only=True)
class StaticReplyConfig(BaseConfig):
    transport_name: str
    reply_text: str


class StaticReplyApplication(BaseWorker):
    """
    A static reply application

    Replies to any inbound message with the configured reply text.
    """

    config: StaticReplyConfig

    async def setup(self) -> None:
        await super().setup()
        self.connector = await self.setup_receive_inbound_connector(
            connector_name=self.config.transport_name,
            inbound_handler=self.handle_inbound_message,
            event_handler=self.handle_event,
        )
        await self.start_consuming()

    async def handle_inbound_message(self, message: Message):
        reply_msg = message.reply(self.config.reply_text, Session.CLOSE)
        await self.connector.publish_outbound(reply_msg)

    async def handle_event(self, event: Event):
        pass
