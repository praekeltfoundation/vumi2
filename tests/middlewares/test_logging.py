import logging

from vumi2.messages import Event, EventType, Message, TransportType
from vumi2.middlewares.logging import LoggingMiddleware, LoggingMiddlewareConfig


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
        inbound_enabled=True,
    )
    middleware = LoggingMiddleware(config)
    await middleware.setup()
    message = mkmsg("Hello")
    assert await middleware.handle_inbound(message, "connection1") == message


async def test_message_is_logged_inbound(caplog):
    """
    This test is to check that the logging message
    is actually logged when handling an inbound message
    """
    config = LoggingMiddlewareConfig(
        "vumi2.middlewares.logging.LoggingMiddleware",
        enable_for_connectors=["connection2"],
        inbound_enabled=True,
    )
    middleware = LoggingMiddleware(config)
    await middleware.setup()
    message = mkmsg("Hello")
    with caplog.at_level(logging.INFO):
        assert await middleware.handle_inbound(message, "connection1") == message
    [log] = [log for log in caplog.records if log.levelno >= logging.INFO]
    log_message = log.getMessage()
    assert "Processed inbound message for connection1" in log_message
    assert "content='Hello'" in log_message


async def test_message_is_logged_outbound(caplog):
    """
    This test is to check that the logging message
    is actually logged when handling an outbound message
    """
    config = LoggingMiddlewareConfig(
        "vumi2.middlewares.logging.LoggingMiddleware",
        enable_for_connectors=["connection2"],
        outbound_enabled=True,
    )
    middleware = LoggingMiddleware(config)
    await middleware.setup()
    message = mkmsg("Goodbye")
    with caplog.at_level(logging.INFO):
        assert await middleware.handle_outbound(message, "connection2") == message
    [log] = [log for log in caplog.records if log.levelno >= logging.INFO]
    log_message = log.getMessage()
    assert "Processed outbound message for connection2" in log_message
    assert "content='Goodbye'" in log_message


async def test_message_is_logged_event(caplog):
    """
    This test is to check that the logging message
    is actually logged when handling an event
    """
    config = LoggingMiddlewareConfig(
        "vumi2.middlewares.logging.LoggingMiddleware",
        enable_for_connectors=["connection2"],
        event_enabled=True,
    )
    middleware = LoggingMiddleware(config)
    await middleware.setup()
    message = mkev("54321")
    with caplog.at_level(logging.INFO):
        assert await middleware.handle_event(message, "connection2") == message
    [log] = [log for log in caplog.records if log.levelno >= logging.INFO]
    log_message = log.getMessage()
    assert "Processed event message for connection2" in log_message
    assert "sent_message_id='54321'" in log_message
