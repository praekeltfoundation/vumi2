from urllib.parse import urlencode

import pytest
from trio import open_memory_channel

from vumi2.messages import EventType, Message, Session, TransportType
from vumi2.transports import AatUssdTransport


@pytest.fixture
def config():
    return AatUssdTransport.CONFIG_CLASS(
        http_bind="localhost",
        base_url="http://www.example.org",
        web_path="/api/aat/ussd",
    )


@pytest.fixture
async def transport(nursery, amqp_connection, config):
    transport = AatUssdTransport(nursery, amqp_connection, config)
    await transport.setup()
    return transport


def create_callback_url(to_addr: str):
    args = urlencode({"to_addr": to_addr})
    return f"http://www.example.org/api/aat/ussd?{args}"


def assert_outbound_message_response(
    body: str, content: str, callback: str, continue_session: bool
):
    # Build this manually for the test, otherwise we'll be doing the same thing in the
    # test and the transport.
    headertext = f"<headertext>{content}</headertext>"

    if continue_session:
        options = (
            "<options>"
            f'<option command="1" order="1" callback="{callback}" display="false" />'
            "</options>"
        )
    else:
        options = ""

    xml = "".join(
        [
            "<request>",
            headertext,
            options,
            "</request>",
        ]
    )

    assert body == xml


async def test_inbound_start_session(transport: AatUssdTransport):
    send_channel, receive_channel = open_memory_channel(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    client = transport.http_app.test_client()
    async with client.request(
        transport.config.web_path,
        query_string={
            "msisdn": "+27820001001",
            "provider": "Vodacom",
            "request": "*1234#",
        },
    ) as connection:
        await connection.send_complete()
        inbound = await receive_channel.receive()

        assert inbound.to_addr == "*1234#"
        assert inbound.from_addr == "+27820001001"
        assert inbound.provider == "Vodacom"
        assert inbound.session_event == Session.NEW

        await ri_connector.publish_outbound(
            Message(
                to_addr=inbound.from_addr,
                from_addr=inbound.to_addr,
                transport_name=inbound.transport_name,
                transport_type=inbound.transport_type,
                in_reply_to=inbound.message_id,
                content="Test response",
                session_event=Session.RESUME,
            )
        )

        response = await connection.receive()
        assert_outbound_message_response(
            response.decode(),
            "Test response",
            create_callback_url(inbound.from_addr),
            continue_session=True,
        )


async def test_close_session(transport: AatUssdTransport):
    send_channel, receive_channel = open_memory_channel(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    client = transport.http_app.test_client()
    async with client.request(
        transport.config.web_path,
        query_string={
            "msisdn": "+27820001001",
            "provider": "Vodacom",
            "request": "*1234#",
        },
    ) as connection:
        await connection.send_complete()
        inbound = await receive_channel.receive()

        await ri_connector.publish_outbound(
            Message(
                to_addr=inbound.from_addr,
                from_addr=inbound.to_addr,
                transport_name=inbound.transport_name,
                transport_type=inbound.transport_type,
                in_reply_to=inbound.message_id,
                content="Test response",
                session_event=Session.CLOSE,
            )
        )

        response = await connection.receive()
        assert_outbound_message_response(
            response.decode(),
            "Test response",
            create_callback_url(inbound.from_addr),
            continue_session=False,
        )


async def test_missing_fields(transport: AatUssdTransport):
    client = transport.http_app.test_client()
    response = await client.get(transport.config.web_path)
    assert response.status_code == 400
    assert await response.json == {
        "missing_parameter": ["msisdn", "provider", "request"]
    }


async def test_inbound_session_resume(transport: AatUssdTransport):
    send_channel, receive_channel = open_memory_channel(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    client = transport.http_app.test_client()
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
        inbound = await receive_channel.receive()

        assert inbound.to_addr == "*1234#"
        assert inbound.from_addr == "+27820001001"
        assert inbound.provider == "Vodacom"
        assert inbound.content == "user response"
        assert inbound.session_event == Session.RESUME

        await ri_connector.publish_outbound(
            Message(
                to_addr=inbound.from_addr,
                from_addr=inbound.to_addr,
                transport_name=inbound.transport_name,
                transport_type=inbound.transport_type,
                in_reply_to=inbound.message_id,
                content="Test response",
                session_event=Session.CLOSE,
            )
        )


async def test_outbound_not_reply(transport: AatUssdTransport):
    send_channel, receive_channel = open_memory_channel(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    outbound = Message(
        to_addr="+27820001001",
        from_addr="*1234#",
        transport_name="aat_ussd",
        transport_type=TransportType.USSD,
    )
    await ri_connector.publish_outbound(outbound)

    event = await receive_channel.receive()
    assert event.event_type == EventType.NACK
    assert event.nack_reason == "Outbound message is not a reply"
    assert event.user_message_id == outbound.message_id


async def test_outbound_no_content(transport: AatUssdTransport):
    send_channel, receive_channel = open_memory_channel(1)

    async def inbound_consumer(msg):
        await send_channel.send(msg)

    ri_connector = await transport.setup_receive_inbound_connector(
        connector_name="http_rpc",
        inbound_handler=inbound_consumer,
        event_handler=inbound_consumer,
    )

    outbound = Message(
        to_addr="+27820001001",
        from_addr="*1234#",
        transport_name="aat_ussd",
        transport_type=TransportType.USSD,
        in_reply_to="testid",
    )
    await ri_connector.publish_outbound(outbound)

    event = await receive_channel.receive()
    assert event.event_type == EventType.NACK
    assert event.nack_reason == "Outbound message has no content"
    assert event.user_message_id == outbound.message_id
