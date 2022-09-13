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
    Should keep track of the vumi message id for each of the smpp message IDs
    """
    await memory_smpp_cache.store_smpp_message_id("vumi", "smpp1")
    assert await memory_smpp_cache.get_smpp_message_id("smpp1") == "vumi"


async def test_in_memory_delete_smpp_message_id(memory_smpp_cache: InMemorySmppCache):
    """
    Deleting should remove from cache
    """
    await memory_smpp_cache.store_smpp_message_id("vumi", "smpp1")
    await memory_smpp_cache.delete_smpp_message_id("smpp1")
    assert await memory_smpp_cache.get_smpp_message_id("smpp1") is None
    # Deleting ID that doesn't exist shouldn't return error
    await memory_smpp_cache.delete_smpp_message_id("invalid")


async def test_in_memory_remove_expired(memory_smpp_cache: InMemorySmppCache):
    """
    If items are old, they should be removed from the cache
    """
    await memory_smpp_cache.store_smpp_message_id("vumi2", "delete")
    await memory_smpp_cache.store_smpp_message_id("vumi1", "keep")
    memory_smpp_cache._smpp_msg_id["delete"] = (
        "vumi2",
        datetime.now() - timedelta(hours=25),
    )
    await memory_smpp_cache._remove_expired()

    assert "keep" in memory_smpp_cache._smpp_msg_id
    assert "delete" not in memory_smpp_cache._smpp_msg_id
