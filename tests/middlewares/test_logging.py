from vumi2.middlewares.logging import LoggingMiddleware, LoggingMiddlewareConfig
from vumi2.messages import Event, EventType, Message, TransportType


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


# Add doc string
async def test_handle_inbound():
    # TODO: make sure we actually log the thing assert that message is logged
    config = LoggingMiddlewareConfig(
        "vumi2.middlewares.logging.LoggingMiddleware",
        enable_for_connectors=["connection2"],
        inbound_enabled=False,
    )
    middleware = LoggingMiddleware(config)
    await middleware.setup()
    message = mkmsg("Hello")
    assert await middleware.handle_inbound(message, "connection1") == message
