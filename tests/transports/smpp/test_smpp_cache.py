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
