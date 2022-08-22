import pytest
from trio import open_memory_channel

from vumi2.messages import EventType, Message, TransportType
from vumi2.transports import HttpRpcTransport


@pytest.fixture
def config():
    return HttpRpcTransport.CONFIG_CLASS(http_bind="localhost")


class OkTransport(HttpRpcTransport):
    async def handle_raw_inbound_message(self, message_id, request):
        await self.connector.publish_inbound(
            Message(
                to_addr="",
                from_addr="",
                transport_name="test",
                transport_type=TransportType.HTTP_API,
                message_id=message_id,
            )
        )


@pytest.fixture
async def transport(nursery, amqp_connection, config):
    transport = OkTransport(nursery, amqp_connection, config)
    await transport.setup()
    return transport


async def test_inbound(transport: OkTransport):
    send_channel, receive_channel = open_memory_channel(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    client = transport.http_app.test_client()
    async with client.request(path="/http_rpc") as connection:
        await connection.send_complete()
        inbound = await receive_channel.receive()
        reply = inbound.reply("test")
        await ri_connector.publish_outbound(reply)
        response = await connection.receive()
        assert response == b"test"

    event = await receive_channel.receive()
    assert event.event_type == EventType.ACK
    assert event.user_message_id == reply.message_id
    assert event.sent_message_id == reply.message_id


async def test_missing_fields_nack(transport: OkTransport):
    send_channel, receive_channel = open_memory_channel(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    outbound = Message(
        to_addr="",
        from_addr="",
        transport_name="",
        transport_type=TransportType.HTTP_API,
    )
    await ri_connector.publish_outbound(outbound)

    with receive_channel:
        event = await receive_channel.receive()
    assert event.event_type == EventType.NACK
    assert event.user_message_id == outbound.message_id
    assert event.sent_message_id == outbound.message_id
    assert event.nack_reason == "Missing fields: in_reply_to, content"


async def test_missing_request_nack(transport: OkTransport):
    send_channel, receive_channel = open_memory_channel(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    outbound = Message(
        to_addr="",
        from_addr="",
        transport_name="",
        transport_type=TransportType.HTTP_API,
        in_reply_to="invalid",
        content="test",
    )
    await ri_connector.publish_outbound(outbound)

    with receive_channel:
        event = await receive_channel.receive()
    assert event.event_type == EventType.NACK
    assert event.user_message_id == outbound.message_id
    assert event.sent_message_id == outbound.message_id
    assert event.nack_reason == "No matching request"
