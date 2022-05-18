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
    consumers = [
        "app1.outbound",
        "app2.outbound",
        "test1.inbound",
        "test1.event",
        "test2.inbound",
        "test2.event",
    ]
    publishers = ["app1.inbound", "app2.inbound", "test1.outbound", "test2.outbound"]
    assert set(router._consumers.keys()) == set(consumers)
    assert set(router._publishers.keys()) == set(publishers)


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

    await router.setup_inbound_consumer("app1", inbound_consumer1)
    await router.setup_inbound_consumer("app2", inbound_consumer2)
    await router.setup_publisher("test1", "inbound")
    await router.publish_inbound_message("test1", msg1)
    await router.publish_inbound_message("test1", msg2)

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

    await router.setup_outbound_consumer("test1", outbound_consumer1)
    await router.setup_outbound_consumer("test2", outbound_consumer2)
    await router.setup_publisher("app1", "outbound")
    await router.publish_outbound_message("app1", msg1)
    await router.publish_outbound_message("app1", msg2)

    async with receive_channel1:
        received_msg1 = await receive_channel1.receive()
    assert msg1 == received_msg1

    async with receive_channel2:
        received_msg2 = await receive_channel2.receive()
    assert msg2 == received_msg2
