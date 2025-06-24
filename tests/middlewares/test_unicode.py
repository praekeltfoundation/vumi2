from vumi2.messages import Event, EventType, Message, TransportType
from vumi2.middlewares.base import BaseMiddlewareConfig
from vumi2.middlewares.unidecoder import Unidecoder


def mkmsg(content: str) -> Message:
    return Message(
        to_addr="12345",
        from_addr="54321",
        transport_name="test",
        transport_type=TransportType.SMS,
        content=content,
    )


def mkev(msg_id: str) -> Event:
    return Event(
        user_message_id=msg_id,
        event_type=EventType.ACK,
        sent_message_id=msg_id,
    )


async def test_unicode_handle_outbound_with_latin_characters():
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.unidecoder.Unidecoder",
        enable_for_connectors=["connection1", "connection2"],
        outbound_enabled=True,
    )
    middleware = Unidecoder(config)
    await middleware.setup()
    message = mkmsg("Goodbye")
    sent_message = await middleware.handle_outbound(message, "connection1")
    assert sent_message.content == "Goodbye"


async def test_unicode_handle_outbound_with_non_latin_characters():
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.unidecoder.Unidecoder",
        enable_for_connectors=["connection1", "connection2"],
        outbound_enabled=True,
    )
    middleware = Unidecoder(config)
    await middleware.setup()
    message = mkmsg("до свидания")
    sent_message = await middleware.handle_outbound(message, "connection1")
    assert sent_message.content == "do svidaniia"
