import logging

from tests.helpers import MiddlewareWorker
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


async def test_logging_with_worker(worker_factory, connector_factory, caplog):
    """
    Adding a logger worker test to test if logger logs correctly
    in actual vumi2
    """
    caplog.set_level(logging.INFO)

    middleware_config = {
        "class_path": "vumi2.middlewares.logging.LoggingMiddleware",
        "enable_for_connectors": ["test", "app"],
        "inbound_enabled": True,
        "outbound_enabled": True,
        "event_enabled": True,
    }
    config = {
        "middlewares": [middleware_config],
    }
    async with worker_factory.with_cleanup(MiddlewareWorker, config) as worker:
        await worker.setup()
        ri_app = await connector_factory.setup_ri("app")
        ro_test = await connector_factory.setup_ro("test")
        message_hello = mkmsg("Hello")
        message_goodbye = mkmsg("GoodBye")
        message_id = message_goodbye.message_id
        print(f"Message Id: {message_id}")
        await ro_test.publish_inbound(message_hello)
        await ri_app.consume_inbound()
        await ri_app.publish_outbound(message_goodbye)
        await ro_test.consume_outbound()
        await ro_test.publish_event(mkev(message_id))
        await ri_app.consume_event()
    assert any(
        "Processed inbound message for test" and "content='Hello'" in log.getMessage()
        for log in caplog.records
    )
    assert any(
        "Processed outbound message for app" and "content='GoodBye'" in log.getMessage()
        for log in caplog.records
    )
    assert any(
        "Processed Processed event message for test"
        and f"sent_message_id='{message_id}'" in log.getMessage()
        for log in caplog.records
    )
