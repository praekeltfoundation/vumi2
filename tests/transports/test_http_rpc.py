from typing import cast

import pytest
from quart_trio.testing.connections import TestHTTPConnection as QuartTestHTTPConnection
from trio import open_memory_channel

from vumi2.messages import EventType, Message, MessageType, TransportType
from vumi2.transports import HttpRpcTransport


def msg_ch_pair(bufsize: int):
    return open_memory_channel[MessageType](bufsize)


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
async def transport(worker_factory):
    config = {"http_bind": "localhost", "request_timeout": 5}
    async with worker_factory(OkTransport, config) as transport:
        await transport.setup()
        yield transport


async def test_inbound(transport: OkTransport):
    send_channel, receive_channel = msg_ch_pair(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    client = transport.http.app.test_client()
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
    send_channel, receive_channel = msg_ch_pair(1)

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
    send_channel, receive_channel = msg_ch_pair(1)

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


async def test_timeout(transport: OkTransport, mock_clock):
    send_channel, receive_channel = msg_ch_pair(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    client = transport.http.app.test_client()
    async with client.request(path="/http_rpc") as connection:
        await connection.send_complete()
        await receive_channel.receive()
        mock_clock.jump(transport.config.request_timeout)
        response = await connection.as_response()
        assert response.status_code == 504
    assert transport.requests == {}
    assert transport.results == {}


async def test_client_disconnect(transport: OkTransport):
    """
    Transport should clean up so that we don't have memory leaks
    """
    send_channel, receive_channel = msg_ch_pair(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    client = transport.http.app.test_client()
    async with client.request(path="/http_rpc") as connection:
        # cast to get access to _client_send private method
        connection = cast(QuartTestHTTPConnection, connection)
        await connection.send_complete()
        await receive_channel.receive()
        await connection.disconnect()
        # It seems like the test client doesn't clean this up properly
        await connection._client_send.aclose()

    assert transport.requests == {}
    assert transport.results == {}
