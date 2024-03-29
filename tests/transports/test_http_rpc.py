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


@pytest.fixture()
async def transport(worker_factory):
    config = {"http_bind": "localhost", "request_timeout": 5}
    async with worker_factory.with_cleanup(OkTransport, config) as transport:
        await transport.setup()
        yield transport


@pytest.fixture()
async def ri_http_rpc(connector_factory):
    # connector_factory handles the necessary cleanup.
    return await connector_factory.setup_ri("http_rpc")


async def test_inbound(transport: OkTransport, ri_http_rpc):
    client = transport.http.app.test_client()
    async with client.request(path="/http_rpc") as connection:
        await connection.send_complete()
        inbound = await ri_http_rpc.consume_inbound()
        reply = inbound.reply("test")
        await ri_http_rpc.publish_outbound(reply)
        response = await connection.receive()
        assert response == b"test"

    event = await ri_http_rpc.consume_event()
    assert event.event_type == EventType.ACK
    assert event.user_message_id == reply.message_id
    assert event.sent_message_id == reply.message_id


async def test_missing_fields_nack(transport: OkTransport, ri_http_rpc):
    outbound = Message(
        to_addr="",
        from_addr="",
        transport_name="",
        transport_type=TransportType.HTTP_API,
    )
    await ri_http_rpc.publish_outbound(outbound)

    event = await ri_http_rpc.consume_event()
    assert event.event_type == EventType.NACK
    assert event.user_message_id == outbound.message_id
    assert event.sent_message_id == outbound.message_id
    assert event.nack_reason == "Missing fields: in_reply_to, content"


async def test_missing_request_nack(transport: OkTransport, ri_http_rpc):
    outbound = Message(
        to_addr="",
        from_addr="",
        transport_name="",
        transport_type=TransportType.HTTP_API,
        in_reply_to="invalid",
        content="test",
    )
    await ri_http_rpc.publish_outbound(outbound)

    event = await ri_http_rpc.consume_event()
    assert event.event_type == EventType.NACK
    assert event.user_message_id == outbound.message_id
    assert event.sent_message_id == outbound.message_id
    assert event.nack_reason == "No matching request"


async def test_timeout(transport: OkTransport, mock_clock, ri_http_rpc):
    client = transport.http.app.test_client()
    async with client.request(path="/http_rpc") as connection:
        await connection.send_complete()
        await ri_http_rpc.consume_inbound()
        mock_clock.jump(transport.config.request_timeout)
        response = await connection.as_response()
        assert response.status_code == 504
    assert transport.requests == {}
    assert transport.results == {}


async def test_client_disconnect(transport: OkTransport, ri_http_rpc):
    """
    Transport should clean up so that we don't have memory leaks
    """
    client = transport.http.app.test_client()
    async with client.request(path="/http_rpc") as connection:
        # cast to get access to _client_send private method
        connection = cast(QuartTestHTTPConnection, connection)
        await connection.send_complete()
        await ri_http_rpc.consume_inbound()
        await connection.disconnect()
        # It seems like the test client doesn't clean this up properly
        await connection._client_send.aclose()

    assert transport.requests == {}
    assert transport.results == {}
