import pytest
from trio import open_memory_channel

from vumi2.messages import Event, EventType, Message, MessageType, TransportType
from vumi2.routers import ToAddressRouter

TEST_CONFIG = {
    "transport_names": ["test1", "test2"],
    "to_address_mappings": {
        "app1": r"^1",
        "app2": r"^2",
    },
}


@pytest.fixture()
async def to_addr_router(worker_factory):
    async with worker_factory(ToAddressRouter, TEST_CONFIG) as worker:
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


async def test_to_addr_router_event(to_addr_router, connector_factory):
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
    ri_app1 = await connector_factory.setup_ri("app1")

    await to_addr_router.handle_outbound_message(outbound)
    await to_addr_router.handle_event(event)

    assert event == await ri_app1.consume_event()


async def test_to_addr_router_inbound(to_addr_router, connector_factory):
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
    ri_app1 = await connector_factory.setup_ri("app1")
    ri_app2 = await connector_factory.setup_ri("app2")
    ro_test1 = await connector_factory.setup_ro("test1")

    await ro_test1.publish_inbound(msg1)
    await ro_test1.publish_inbound(msg2)

    assert msg1 == await ri_app1.consume_inbound()
    assert msg2 == await ri_app2.consume_inbound()


async def test_to_addr_router_outbound(to_addr_router, connector_factory):
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
    ri_app1 = await connector_factory.setup_ri("app1")
    ro_test1 = await connector_factory.setup_ro("test1")
    ro_test2 = await connector_factory.setup_ro("test2")

    await ri_app1.publish_outbound(msg1)
    await ri_app1.publish_outbound(msg2)

    assert msg1 == await ro_test1.consume_outbound()
    assert msg2 == await ro_test2.consume_outbound()
