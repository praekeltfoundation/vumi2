import async_amqp
import pytest
from trio import open_memory_channel

from vumi2.messages import Event, EventType, Message, TransportType
from vumi2.routers import ToAddressRouter


@pytest.fixture
async def amqp_connection():
    # TODO: config
    async with async_amqp.connect_amqp() as connection:
        yield connection


async def test_to_addr_router_setup(amqp_connection):
    """
    Sets up all the consumers and publishers according to the config
    """
    router = ToAddressRouter(amqp_connection)
    await router.setup()
    receive_inbound_connectors = [
        "test1",
        "test2",
    ]
    receive_outbound_connectors = ["app1", "app2"]
    assert set(router.receive_inbound_connectors.keys()) == set(
        receive_inbound_connectors
    )
    assert set(router.receive_outbound_connectors.keys()) == set(
        receive_outbound_connectors
    )


async def test_to_addr_router_event(amqp_connection):
    """
    Events should be ignored
    """
    router = ToAddressRouter(amqp_connection)
    await router.setup()
    event = Event(
        user_message_id="1",
        event_type=EventType.ACK,
        sent_message_id="1",
    )
    await router.handle_event(event)


async def test_to_addr_router_inbound(amqp_connection):
    """
    Should be routed according to the to address
    """
    router = ToAddressRouter(amqp_connection)
    await router.setup()
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
    send_channel1, receive_channel1 = open_memory_channel(1)
    send_channel2, receive_channel2 = open_memory_channel(1)

    async def inbound_consumer1(msg):
        with send_channel1:
            await send_channel1.send(msg)

    async def inbound_consumer2(msg):
        with send_channel2:
            await send_channel2.send(msg)

    connector1 = await router.setup_receive_inbound_connector("app1")
    connector1.set_inbound_handler(inbound_consumer1)
    connector2 = await router.setup_receive_inbound_connector("app2")
    connector2.set_inbound_handler(inbound_consumer2)
    transport_connector = await router.setup_receive_outbound_connector("test1")
    await transport_connector.publish_inbound(msg1)
    await transport_connector.publish_inbound(msg2)

    async with receive_channel1:
        received_msg1 = await receive_channel1.receive()
    assert msg1 == received_msg1

    async with receive_channel2:
        received_msg2 = await receive_channel2.receive()
    assert msg2 == received_msg2


async def test_to_addr_router_outbound(amqp_connection):
    """
    Should be routed according to the transport_name
    """
    router = ToAddressRouter(amqp_connection)
    await router.setup()
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
    send_channel1, receive_channel1 = open_memory_channel(1)
    send_channel2, receive_channel2 = open_memory_channel(1)

    async def outbound_consumer1(msg):
        with send_channel1:
            await send_channel1.send(msg)

    async def outbound_consumer2(msg):
        with send_channel2:
            await send_channel2.send(msg)

    connector1 = await router.setup_receive_outbound_connector("test1")
    connector1.set_outbound_handler(outbound_consumer1)
    connector2 = await router.setup_receive_outbound_connector("test2")
    connector2.set_outbound_handler(outbound_consumer2)
    app_connector = await router.setup_receive_inbound_connector("app1")
    await app_connector.publish_outbound(msg1)
    await app_connector.publish_outbound(msg2)

    async with receive_channel1:
        received_msg1 = await receive_channel1.receive()
    assert msg1 == received_msg1

    async with receive_channel2:
        received_msg2 = await receive_channel2.receive()
    assert msg2 == received_msg2
