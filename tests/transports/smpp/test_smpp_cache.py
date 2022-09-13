from datetime import datetime, timedelta

import pytest

from vumi2.transports.smpp.smpp_cache import InMemorySmppCache


@pytest.fixture
async def memory_smpp_cache():
    return InMemorySmppCache({})


async def test_in_memory_store_multipart(memory_smpp_cache: InMemorySmppCache):
    """
    Should store the parts one at a time, and when we have them all, return the
    combined message
    """
    assert await memory_smpp_cache.store_multipart(1, 3, 1, "part1") is None
    assert await memory_smpp_cache.store_multipart(1, 3, 2, "part2") is None
    combined = await memory_smpp_cache.store_multipart(1, 3, 3, "part3")
    assert combined == "part1part2part3"


async def test_in_memory_delivery_report(memory_smpp_cache: InMemorySmppCache):
    """
    Should keep track of the smpp message id, vumi message id, and delivery reports,
    to know when we've seen them all and can send a success delivery report
    """
    await memory_smpp_cache.store_smpp_message_id(2, "vumi", "smpp1")
    # Haven't seen all the parts yet
    assert await memory_smpp_cache.seen_success_delivery_report("smpp1") is False

    # If it's not in the cache, no error
    await memory_smpp_cache.seen_success_delivery_report("invalid")

    await memory_smpp_cache.store_smpp_message_id(2, "vumi", "smpp2")
    assert await memory_smpp_cache.seen_success_delivery_report("smpp2") is True


async def test_in_memory_delete_smpp_message_id(memory_smpp_cache: InMemorySmppCache):
    """
    Deleting should also remove all other related message IDs
    """
    await memory_smpp_cache.store_smpp_message_id(2, "vumi", "smpp1")
    await memory_smpp_cache.store_smpp_message_id(2, "vumi", "smpp2")
    assert "smpp1" in memory_smpp_cache._smpp_msg_id
    assert "smpp2" in memory_smpp_cache._smpp_msg_id
    assert "vumi" in memory_smpp_cache._vumi_msg_dr

    await memory_smpp_cache.delete_smpp_message_id("smpp1")
    assert "smpp1" not in memory_smpp_cache._smpp_msg_id
    assert "smpp2" not in memory_smpp_cache._smpp_msg_id
    assert "vumi" not in memory_smpp_cache._vumi_msg_dr


async def test_in_memory_remove_expired(memory_smpp_cache: InMemorySmppCache):
    """
    If items are old, they should be removed from the cache
    """
    await memory_smpp_cache.store_smpp_message_id(1, "vumi2", "delete")
    await memory_smpp_cache.store_smpp_message_id(1, "vumi1", "keep")
    memory_smpp_cache._smpp_msg_id["delete"] = (
        "vumi2",
        1,
        datetime.now() - timedelta(hours=25),
    )
    await memory_smpp_cache._remove_expired()

    assert "vumi1" in memory_smpp_cache._vumi_msg_dr
    assert "vumi2" not in memory_smpp_cache._vumi_msg_dr
    assert "keep" in memory_smpp_cache._smpp_msg_id
    assert "delete" not in memory_smpp_cache._smpp_msg_id
