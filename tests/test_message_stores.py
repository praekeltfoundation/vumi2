from datetime import datetime, timedelta

import pytest

from vumi2.message_stores import MemoryMessageStore, MemoryMessageStoreConfig
from vumi2.messages import Message, TransportType


@pytest.fixture
def memory_message_store():
    config = MemoryMessageStoreConfig.deserialise({})
    return MemoryMessageStore(config)


def create_outbound(timestamp: datetime) -> Message:
    return Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="bulk_sms",
        transport_type=TransportType.SMS,
        timestamp=timestamp,
    )


async def test_memory_store_and_fetch(memory_message_store: MemoryMessageStore):
    outbound = create_outbound(datetime.utcnow())
    await memory_message_store.store_outbound(outbound)
    returned_outbound = await memory_message_store.fetch_outbound(outbound.message_id)
    assert outbound == returned_outbound


async def test_memory_timeout(memory_message_store: MemoryMessageStore):
    expired_outbound = create_outbound(
        datetime.utcnow() - timedelta(seconds=memory_message_store.config.timeout + 1)
    )
    keep_outbound = create_outbound(datetime.utcnow())

    await memory_message_store.store_outbound(expired_outbound)
    await memory_message_store.store_outbound(keep_outbound)

    assert (
        await memory_message_store.fetch_outbound(expired_outbound.message_id) is None
    )
    assert (
        await memory_message_store.fetch_outbound(keep_outbound.message_id)
        == keep_outbound
    )
