import pytest
import trio

from vumi2.message_caches import MemoryMessageCache, TimeoutDict
from vumi2.messages import Message, TransportType


@pytest.fixture()
def memory_message_cache():
    return MemoryMessageCache({})


def create_outbound() -> Message:
    return Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="bulk_sms",
        transport_type=TransportType.SMS,
    )


def create_inbound() -> Message:
    return Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="bulk_sms",
        transport_type=TransportType.SMS,
    )


async def test_timeoutdict_mutablemapping_api(autojump_clock):
    """
    TimeoutDict.__iter__ and TimeoutDict.__len__ need to be implemented
    for MutableMapping, but we don't actually use them anywhere.

    The autojump_clock fixture injects a fake clock that skips ahead
    when there aren't any active tasks, so we can sleep as long as we
    like without any real-world time passing.
    """
    td: TimeoutDict[str] = TimeoutDict(timeout=5)
    assert len(td) == 0
    assert list(iter(td)) == []

    td["a"] = "1"
    await trio.sleep(2)
    td["b"] = "2"

    assert len(td) == 2
    assert list(iter(td)) == ["a", "b"]

    await trio.sleep(4)
    assert len(td) == 1
    assert list(iter(td)) == ["b"]


async def test_memory_cache_and_fetch_outbound(
    memory_message_cache: MemoryMessageCache,
):
    """
    Caching a message and then fetching it later should return the same message
    """
    outbound = create_outbound()
    await memory_message_cache.store_outbound(outbound)
    returned_outbound = await memory_message_cache.fetch_outbound(outbound.message_id)
    assert outbound == returned_outbound


async def test_memory_timeout_outbound(
    memory_message_cache: MemoryMessageCache, autojump_clock
):
    """
    Messages that are older than the configered timeout should be removed from the cache
    """
    oldmsg = create_outbound()
    await memory_message_cache.store_outbound(oldmsg)
    await trio.sleep(memory_message_cache.config.timeout - 1)

    newmsg = create_outbound()
    await memory_message_cache.store_outbound(newmsg)

    # Neither message has expired yet, although oldmsg is close.
    assert await memory_message_cache.fetch_outbound(oldmsg.message_id) == oldmsg
    assert await memory_message_cache.fetch_outbound(newmsg.message_id) == newmsg

    # Wait until oldmsg expires, leaving only newmsg.
    await trio.sleep(2)
    assert await memory_message_cache.fetch_outbound(oldmsg.message_id) is None
    assert await memory_message_cache.fetch_outbound(newmsg.message_id) == newmsg


async def test_memory_cache_and_fetch_inbound(
    memory_message_cache: MemoryMessageCache,
):
    """
    Caching a message and then fetching it later should return the same message
    """
    inbound = create_inbound()
    await memory_message_cache.store_inbound(inbound)
    returned_inbound = await memory_message_cache.fetch_inbound(inbound.message_id)
    assert inbound == returned_inbound

    returned_inbound = await memory_message_cache.fetch_last_inbound_by_from_address(
        inbound.from_addr
    )
    assert inbound == returned_inbound


async def test_memory_timeout_inbound(
    memory_message_cache: MemoryMessageCache,
    autojump_clock,
):
    """
    Messages that are older than the configered timeout should be removed from the cache
    """
    oldmsg = create_inbound()
    await memory_message_cache.store_inbound(oldmsg)
    await trio.sleep(memory_message_cache.config.timeout - 1)

    newmsg = create_inbound()
    await memory_message_cache.store_inbound(newmsg)

    # Message has not expired yet.
    assert await memory_message_cache.fetch_inbound(oldmsg.message_id) == oldmsg
    assert await memory_message_cache.fetch_inbound(newmsg.message_id) == newmsg
    assert (
        await memory_message_cache.fetch_last_inbound_by_from_address(oldmsg.from_addr)
        == newmsg
    )

    # Wait until oldmsg expires.
    await trio.sleep(2)
    assert await memory_message_cache.fetch_inbound(oldmsg.message_id) is None
    assert await memory_message_cache.fetch_inbound(newmsg.message_id) == newmsg
    assert (
        await memory_message_cache.fetch_last_inbound_by_from_address(oldmsg.from_addr)
        == newmsg
    )
