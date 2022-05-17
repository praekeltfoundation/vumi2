from asyncio import Future

from vumi2.utils import maybe_awaitable


async def test_maybe_awaitable():
    assert await maybe_awaitable(1) == 1
    f = Future()
    f.set_result(1)
    assert await maybe_awaitable(f) == 1
