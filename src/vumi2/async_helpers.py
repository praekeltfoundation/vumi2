from collections.abc import Awaitable
from inspect import isawaitable
from typing import TypeVar, Union, cast

_RETURN = TypeVar("_RETURN")


async def maybe_awaitable(value: Union[_RETURN, Awaitable[_RETURN]]) -> _RETURN:
    if isawaitable(value):
        # cast because isawaitable doesn't properly narrow the type
        return await cast(Awaitable[_RETURN], value)
    return cast(_RETURN, value)
