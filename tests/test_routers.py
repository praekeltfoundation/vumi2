import pytest
from trio import WouldBlock, open_memory_channel

from vumi2.messages import Event, EventType, Message, MessageType, TransportType
from vumi2.routers import ToAddressRouter

TEST_CONFIG = {
    "transport_names": ["test1", "test2"],
    "to_address_mappings": [
        {"name": "app1", "pattern": r"^1"},
        {"name": "app2", "pattern": r"^2"},
    ],
    "default_app": "app3",
}


@pytest.fixture()
async def to_addr_router(worker_factory):
    async with worker_factory.with_cleanup(ToAddressRouter, TEST_CONFIG) as worker:
        yield worker


@pytest.fixture()
async def to_addr_router_no_default(worker_factory):
    new_config = TEST_CONFIG.copy()
    del new_config["default_app"]
    async with worker_factory.with_cleanup(ToAddressRouter, new_config) as worker:
        yield worker


@pytest.fixture()
async def to_addr_router_duplicate_default(worker_factory):
    new_config = TEST_CONFIG.copy()
    new_config["default_app"] = "app1"
    async with worker_factory.with_cleanup(ToAddressRouter, new_config) as worker:
        yield worker


@pytest.fixture()
async def to_addr_router_multiple_matches(worker_factory):
    new_config = TEST_CONFIG.copy()
    new_config["to_address_mappings"] = [
        {"name": "app1", "pattern": r"^1"},
        {"name": "app2", "pattern": r"^1"},
    ]
    async with worker_factory.with_cleanup(ToAddressRouter, new_config) as worker:
        yield worker


def msg_ch_pair(bufsize: int):
    return open_memory_channel[MessageType](bufsize)


async def ignore_message(_: MessageType) -> None:
    return


async def test_to_addr_router_setup1(to_addr_router):
    """
    Sets up all the consumers and publishers according to the config
    """
    await to_addr_router.setup()
    receive_inbound_connectors = ["test1", "test2"]
    receive_outbound_connectors = ["app1", "app2", "app3"]
    assert set(to_addr_router.receive_inbound_connectors.keys()) == set(
        receive_inbound_connectors
    )
    assert set(to_addr_router.receive_outbound_connectors.keys()) == set(
        receive_outbound_connectors
    )


async def test_to_addr_router_setup_duplicate_default(to_addr_router_duplicate_default):
    """
    It should not attempt to add a duplicate connector if the default app is one of the
    to address mappings
    """
    await to_addr_router_duplicate_default.setup()
    receive_inbound_connectors = ["test1", "test2"]
    receive_outbound_connectors = ["app1", "app2"]
    assert set(
        to_addr_router_duplicate_default.receive_inbound_connectors.keys()
    ) == set(receive_inbound_connectors)
    assert set(
        to_addr_router_duplicate_default.receive_outbound_connectors.keys()
    ) == set(receive_outbound_connectors)


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


async def test_to_addr_router_event_default_app(to_addr_router, connector_factory):
    """
    Events that have an outbound in the store should be routed on the default app if
    there is one configured and no mappings match
    """
    await to_addr_router.setup()
    outbound = Message(
        to_addr="+27820001001",
        from_addr="33345",
        transport_name="test1",
        transport_type=TransportType.SMS,
    )
    event = Event(
        user_message_id=outbound.message_id,
        event_type=EventType.ACK,
        sent_message_id=outbound.message_id,
    )
    ri_app3 = await connector_factory.setup_ri("app3")

    await to_addr_router.handle_outbound_message(outbound)
    await to_addr_router.handle_event(event)

    assert event == await ri_app3.consume_event()


async def test_to_addr_router_event_no_default(
    to_addr_router_no_default, connector_factory
):
    """
    Events that have an outbound in the store should not be routed if they don't match
    any mappings and there is no default app
    """
    await to_addr_router_no_default.setup()
    outbound = Message(
        to_addr="+27820001001",
        from_addr="33345",
        transport_name="test1",
        transport_type=TransportType.SMS,
    )
    event = Event(
        user_message_id=outbound.message_id,
        event_type=EventType.ACK,
        sent_message_id=outbound.message_id,
    )
    ri_app3 = await connector_factory.setup_ri("app3")

    await to_addr_router_no_default.handle_outbound_message(outbound)
    await to_addr_router_no_default.handle_event(event)

    with pytest.raises(WouldBlock):
        await ri_app3.consume_event_nowait()


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
    msg3 = Message(
        to_addr="33345",
        from_addr="54321",
        transport_name="test",
        transport_type=TransportType.SMS,
    )
    ri_app1 = await connector_factory.setup_ri("app1")
    ri_app2 = await connector_factory.setup_ri("app2")
    ri_app3 = await connector_factory.setup_ri("app3")
    ro_test1 = await connector_factory.setup_ro("test1")

    await ro_test1.publish_inbound(msg1)
    await ro_test1.publish_inbound(msg2)
    await ro_test1.publish_inbound(msg3)

    assert msg1 == await ri_app1.consume_inbound()
    assert msg2 == await ri_app2.consume_inbound()
    assert msg3 == await ri_app3.consume_inbound()


async def test_to_addr_router_inbound_no_default(
    to_addr_router_no_default, connector_factory
):
    """
    Should not be routed according if the to_addr doesn't match any mappings and there
    isn't a default app configured.
    """
    await to_addr_router_no_default.setup()
    msg1 = Message(
        to_addr="12345",
        from_addr="54321",
        transport_name="test",
        transport_type=TransportType.SMS,
    )
    msg2 = Message(
        to_addr="33345",
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

    with pytest.raises(WouldBlock):
        await ri_app2.consume_inbound_nowait()


async def test_to_addr_router_inbound_multiple_matches(
    to_addr_router_multiple_matches, connector_factory
):
    """
    Should only send message to the first app where the mapping regex matches
    """
    await to_addr_router_multiple_matches.setup()
    msg = Message(
        to_addr="12345",
        from_addr="54321",
        transport_name="test",
        transport_type=TransportType.SMS,
    )
    ri_app1 = await connector_factory.setup_ri("app1")
    ri_app2 = await connector_factory.setup_ri("app2")

    await to_addr_router_multiple_matches.handle_inbound_message(msg)
    assert msg == await ri_app1.consume_inbound()
    with pytest.raises(WouldBlock):
        assert msg == await ri_app2.consume_inbound_nowait()


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
