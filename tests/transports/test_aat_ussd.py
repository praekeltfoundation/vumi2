from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import pytest
from trio import open_memory_channel

from vumi2.messages import EventType, Message, MessageType, Session, TransportType
from vumi2.transports import AatUssdTransport


def msg_ch_pair(bufsize: int):
    return open_memory_channel[MessageType](bufsize)


@pytest.fixture()
async def transport(worker_factory):
    config = {
        "http_bind": "localhost",
        "base_url": "http://www.example.org",
        "web_path": "/api/aat/ussd",
    }
    async with worker_factory.with_cleanup(AatUssdTransport, config) as transport:
        await transport.setup()
        yield transport


@pytest.fixture()
async def ri_http_rpc(connector_factory):
    # connector_factory handles the necessary cleanup.
    return await connector_factory.setup_ri("http_rpc")


def create_callback_url(to_addr: str):
    args = urlencode({"to_addr": to_addr})
    return f"http://www.example.org/api/aat/ussd?{args}"


def assert_outbound_message_response(
    body: str, content: str, callback: str, continue_session: bool
):
    root = ET.fromstring(body)  # noqa: S314 (This is trusted XML.)
    assert root.tag == "request"

    headertext = root[0]
    assert headertext.tag == "headertext"
    assert headertext.text == content

    if continue_session:
        options = root[1]
        assert options.tag == "options"
        [option] = list(options)
        assert option.tag == "option"
        assert option.attrib == {
            "command": "1",
            "order": "1",
            "callback": callback,
            "display": "false",
        }


async def test_inbound_start_session(transport: AatUssdTransport, ri_http_rpc):
    client = transport.http.app.test_client()
    async with client.request(
        transport.config.web_path,
        query_string={
            "msisdn": "+27820001001",
            "provider": "Vodacom",
            "request": "*1234#",
        },
    ) as connection:
        await connection.send_complete()
        inbound = await ri_http_rpc.consume_inbound()

        assert inbound.to_addr == "*1234#"
        assert inbound.from_addr == "+27820001001"
        assert inbound.provider == "Vodacom"
        assert inbound.session_event == Session.NEW
        assert inbound.content == "*1234#"

        reply = inbound.reply("Test response")
        await ri_http_rpc.publish_outbound(reply)

        response = await connection.receive()
        assert_outbound_message_response(
            response.decode(),
            "Test response",
            create_callback_url(inbound.to_addr),
            continue_session=True,
        )

    ack = await ri_http_rpc.consume_event()
    assert ack.event_type == EventType.ACK
    assert ack.user_message_id == reply.message_id


async def test_close_session(transport: AatUssdTransport, ri_http_rpc):
    client = transport.http.app.test_client()
    async with client.request(
        transport.config.web_path,
        query_string={
            "msisdn": "+27820001001",
            "provider": "Vodacom",
            "request": "*1234#",
        },
    ) as connection:
        await connection.send_complete()
        inbound = await ri_http_rpc.consume_inbound()

        reply = inbound.reply("Test response", Session.CLOSE)
        await ri_http_rpc.publish_outbound(reply)

        response = await connection.receive()
        assert_outbound_message_response(
            response.decode(),
            "Test response",
            create_callback_url(inbound.from_addr),
            continue_session=False,
        )

    ack = await ri_http_rpc.consume_event()
    assert ack.event_type == EventType.ACK
    assert ack.user_message_id == reply.message_id


async def test_missing_fields(transport: AatUssdTransport):
    client = transport.http.app.test_client()
    response = await client.get(transport.config.web_path)
    assert response.status_code == 400
    assert await response.json == {
        "missing_parameter": ["msisdn", "provider", "request"]
    }


async def test_inbound_session_resume(transport: AatUssdTransport, ri_http_rpc):
    client = transport.http.app.test_client()
    async with client.request(
        transport.config.web_path,
        query_string={
            "msisdn": "+27820001001",
            "provider": "Vodacom",
            "request": "user response",
            "to_addr": "*1234#",
        },
    ) as connection:
        await connection.send_complete()
        inbound = await ri_http_rpc.consume_inbound()

        assert inbound.to_addr == "*1234#"
        assert inbound.from_addr == "+27820001001"
        assert inbound.provider == "Vodacom"
        assert inbound.content == "user response"
        assert inbound.session_event == Session.RESUME

        reply = inbound.reply("Test response")
        await ri_http_rpc.publish_outbound(reply)

    ack = await ri_http_rpc.consume_event()
    assert ack.event_type == EventType.ACK
    assert ack.user_message_id == reply.message_id


async def test_outbound_not_reply(transport: AatUssdTransport, ri_http_rpc):
    outbound = Message(
        to_addr="+27820001001",
        from_addr="*1234#",
        transport_name="aat_ussd",
        transport_type=TransportType.USSD,
    )
    await ri_http_rpc.publish_outbound(outbound)

    event = await ri_http_rpc.consume_event()
    assert event.event_type == EventType.NACK
    assert event.nack_reason == "Outbound message is not a reply"
    assert event.user_message_id == outbound.message_id


async def test_outbound_no_content(transport: AatUssdTransport, ri_http_rpc):
    outbound = Message(
        to_addr="+27820001001",
        from_addr="*1234#",
        transport_name="aat_ussd",
        transport_type=TransportType.USSD,
        in_reply_to="testid",
    )
    await ri_http_rpc.publish_outbound(outbound)

    event = await ri_http_rpc.consume_event()
    assert event.event_type == EventType.NACK
    assert event.nack_reason == "Outbound message has no content"
    assert event.user_message_id == outbound.message_id
