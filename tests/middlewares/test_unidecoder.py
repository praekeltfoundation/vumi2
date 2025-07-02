from tests.helpers import MiddlewareWorker
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


async def test_unicoder_with_worker(worker_factory, connector_factory):
    middleware_config = {
        "class_path": "vumi2.middlewares.unidecoder.Unidecoder",
        "enable_for_connectors": ["test", "app"],
        "inbound_enabled": False,
        "outbound_enabled": True,
        "event_enabled": False,
    }
    config = {
        "middlewares": [middleware_config],
    }
    async with worker_factory.with_cleanup(MiddlewareWorker, config) as worker:
        await worker.setup()
        ri_app = await connector_factory.setup_ri("app")
        ro_test = await connector_factory.setup_ro("test")
        message_hello = mkmsg("Hello")
        message_goodbye = mkmsg("до свидания")
        message_id = message_goodbye.message_id
        await ro_test.publish_inbound(message_hello)
        message_hello_test = await ri_app.consume_inbound()
        await ri_app.publish_outbound(message_goodbye)
        message_goodbye_test = await ro_test.consume_outbound()
        await ro_test.publish_event(mkev(message_id))
        event = await ri_app.consume_event()
    assert message_hello_test.content == "Hello"
    assert message_goodbye_test.content == "do svidaniia"
    assert event.helper_metadata == {}
