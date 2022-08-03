from inspect import isawaitable

from vumi2.async_helpers import maybe_awaitable


async def test_maybe_awaitable():
    assert await maybe_awaitable(1) == 1

    async def f():
        return 1

    coro = f()
    assert isawaitable(coro)
    assert await maybe_awaitable(coro) == 1
