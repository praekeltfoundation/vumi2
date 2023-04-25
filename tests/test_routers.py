import pytest
from trio import open_memory_channel

from vumi2.messages import Event, EventType, Message, MessageType, TransportType
from vumi2.routers import ToAddressRouter

from .helpers import worker_with_cleanup

TEST_CONFIG = {
    "transport_names": ["test1", "test2"],
    "to_address_mappings": {
        "app1": r"^1",
        "app2": r"^2",
    },
}


@pytest.fixture
async def to_addr_router(request, nursery, amqp_connection):
    async with worker_with_cleanup(
        request,
        nursery,
        amqp_connection,
        ToAddressRouter,
        TEST_CONFIG,
    ) as worker:
        yield worker


def msg_ch_pair(bufsize: int):
    return open_memory_channel[MessageType](bufsize)


async def ignore_message(_: MessageType) -> None:
    return


async def test_to_addr_router_setup(to_addr_router):
    """
    Sets up all the consumers and publishers according to the config
    """
    await to_addr_router.setup()
    receive_inbound_connectors = ["test1", "test2"]
    receive_outbound_connectors = ["app1", "app2"]
    assert set(to_addr_router.receive_inbound_connectors.keys()) == set(
        receive_inbound_connectors
    )
    assert set(to_addr_router.receive_outbound_connectors.keys()) == set(
        receive_outbound_connectors
    )


async def test_to_addr_router_event_no_routing(to_addr_router):
    """
    Events that don't have an outbound in the store should be ignored
    """
    await to_addr_router.setup()
    event = Event(
        user_message_id="1",
        event_type=EventType.ACK,
        sent_message_id="1",
    )
    await to_addr_router.handle_event(event)


async def test_to_addr_router_event(to_addr_router):
    """
    Events that have an outbound in the store should be routed
    """
    await to_addr_router.setup()
    outbound = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="test1",
        transport_type=TransportType.SMS,
    )
    event = Event(
        user_message_id=outbound.message_id,
        event_type=EventType.ACK,
        sent_message_id=outbound.message_id,
    )
    send_channel, receive_channel = msg_ch_pair(1)

    async def consumer(msg):
        with send_channel:
            await send_channel.send(msg)

    await to_addr_router.setup_receive_inbound_connector(
        connector_name="app1",
        inbound_handler=consumer,
        event_handler=consumer,
    )

    await to_addr_router.handle_outbound_message(outbound)
    await to_addr_router.handle_event(event)

    async with receive_channel:
        received_event = await receive_channel.receive()
    assert event == received_event


async def test_to_addr_router_inbound(to_addr_router):
    """
    Should be routed according to the to address
    """
    await to_addr_router.setup()
    msg1 = Message(
        to_addr="12345",
        from_addr="54321",
        transport_name="test",
        transport_type=TransportType.SMS,
    )
    msg2 = Message(
        to_addr="22345",
        from_addr="54321",
        transport_name="test",
        transport_type=TransportType.SMS,
    )
    send_channel1, receive_channel1 = msg_ch_pair(1)
    send_channel2, receive_channel2 = msg_ch_pair(1)

    async def inbound_consumer1(msg):
        with send_channel1:
            await send_channel1.send(msg)

    async def inbound_consumer2(msg):
        with send_channel2:
            await send_channel2.send(msg)

    await to_addr_router.setup_receive_inbound_connector(
        connector_name="app1",
        inbound_handler=inbound_consumer1,
        event_handler=ignore_message,
    )
    await to_addr_router.setup_receive_inbound_connector(
        connector_name="app2",
        inbound_handler=inbound_consumer2,
        event_handler=ignore_message,
    )
    transport_connector = await to_addr_router.setup_receive_outbound_connector(
        connector_name="test1", outbound_handler=ignore_message
    )
    await transport_connector.publish_inbound(msg1)
    await transport_connector.publish_inbound(msg2)

    async with receive_channel1:
        received_msg1 = await receive_channel1.receive()
    assert msg1 == received_msg1

    async with receive_channel2:
        received_msg2 = await receive_channel2.receive()
    assert msg2 == received_msg2


async def test_to_addr_router_outbound(to_addr_router):
    """
    Should be routed according to the transport_name
    """
    await to_addr_router.setup()
    msg1 = Message(
        to_addr="12345",
        from_addr="54321",
        transport_name="test1",
        transport_type=TransportType.SMS,
    )
    msg2 = Message(
        to_addr="22345",
        from_addr="54321",
        transport_name="test2",
        transport_type=TransportType.SMS,
    )
    send_channel1, receive_channel1 = msg_ch_pair(1)
    send_channel2, receive_channel2 = msg_ch_pair(1)

    async def outbound_consumer1(msg):
        with send_channel1:
            await send_channel1.send(msg)

    async def outbound_consumer2(msg):
        with send_channel2:
            await send_channel2.send(msg)

    await to_addr_router.setup_receive_outbound_connector(
        connector_name="test1", outbound_handler=outbound_consumer1
    )
    await to_addr_router.setup_receive_outbound_connector(
        connector_name="test2", outbound_handler=outbound_consumer2
    )
    app_connector = await to_addr_router.setup_receive_inbound_connector(
        connector_name="app1",
        inbound_handler=ignore_message,
        event_handler=ignore_message,
    )
    await app_connector.publish_outbound(msg1)
    await app_connector.publish_outbound(msg2)

    async with receive_channel1:
        received_msg1 = await receive_channel1.receive()
    assert msg1 == received_msg1

    async with receive_channel2:
        received_msg2 = await receive_channel2.receive()
    assert msg2 == received_msg2
