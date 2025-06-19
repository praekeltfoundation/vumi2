from vumi2.messages import Event, EventType, Message, TransportType
from vumi2.middlewares.base import BaseMiddleware, BaseMiddlewareConfig


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


async def test_base_inbound_enabled():
    """
    Test for basemiddle ware when inbound connections are enabled on a connection
    """
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        inbound_enabled=True,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert basemiddleware.inbound_enabled("connection1") is False
    assert basemiddleware.inbound_enabled("connection2") is True


async def test_base_inbound_disabled():
    """
    Test for basemiddle ware when inbound connections are disabled on a connection
    """
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        inbound_enabled=False,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert basemiddleware.inbound_enabled("connection1") is False
    assert basemiddleware.inbound_enabled("connection2") is False


async def test_base_outbound_enabled():
    """
    Test for basemiddle ware when inbound connections are enabled on a connection
    """
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        outbound_enabled=True,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert basemiddleware.outbound_enabled("connection1") is False
    assert basemiddleware.outbound_enabled("connection2") is True


async def test_base_outbound_disabled():
    """
    Test for basemiddle ware when inbound connections are disabled on a connection
    """
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        outbound_enabled=False,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert basemiddleware.outbound_enabled("connection1") is False
    assert basemiddleware.outbound_enabled("connection2") is False


async def test_base_event_enabled():
    """
    Test for basemiddle ware when inbound connections are enabled on a connection
    """
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        event_enabled=True,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert basemiddleware.event_enabled("connection1") is False
    assert basemiddleware.event_enabled("connection2") is True


async def test_base_event_disabled():
    """
    Test for basemiddle ware when inbound connections are disabled on a connection
    """
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        event_enabled=False,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert basemiddleware.event_enabled("connection1") is False
    assert basemiddleware.event_enabled("connection2") is False


async def test_base_handle_inbound():
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        inbound_enabled=False,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    message = mkmsg("Hello")
    assert await basemiddleware.handle_inbound(message, "connection1") == message


async def test_base_handle_outbound():
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        outbound_enabled=True,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    message = mkmsg("Hello")
    assert await basemiddleware.handle_outbound(message, "connection1") == message


async def test_base_handle_event():
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        event_enabled=True,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    event = mkev("Hello")
    assert await basemiddleware.handle_outbound(event, "connection1") == event
