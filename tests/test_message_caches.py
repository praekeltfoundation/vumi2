from datetime import datetime, timedelta

import pytest

from vumi2.message_caches import MemoryMessageCache
from vumi2.messages import Message, TransportType


@pytest.fixture()
def memory_message_cache():
    return MemoryMessageCache({})


def create_outbound(timestamp: datetime) -> Message:
    return Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="bulk_sms",
        transport_type=TransportType.SMS,
        timestamp=timestamp,
    )


async def test_memory_cache_and_fetch(memory_message_cache: MemoryMessageCache):
    """
    Caching a message and then fetching it later should return the same message
    """
    outbound = create_outbound(datetime.utcnow())
    await memory_message_cache.store_outbound(outbound)
    returned_outbound = await memory_message_cache.fetch_outbound(outbound.message_id)
    assert outbound == returned_outbound


async def test_memory_timeout(memory_message_cache: MemoryMessageCache):
    """
    Messages that are older than the configered timeout should be removed from the cache
    """
    expired_outbound = create_outbound(
        datetime.utcnow() - timedelta(seconds=memory_message_cache.config.timeout + 1)
    )
    keep_outbound = create_outbound(datetime.utcnow())

    await memory_message_cache.store_outbound(expired_outbound)
    await memory_message_cache.store_outbound(keep_outbound)

    assert (
        await memory_message_cache.fetch_outbound(expired_outbound.message_id) is None
    )
    assert (
        await memory_message_cache.fetch_outbound(keep_outbound.message_id)
        == keep_outbound
    )
