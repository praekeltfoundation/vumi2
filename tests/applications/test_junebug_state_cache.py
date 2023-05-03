import trio

from vumi2.applications.junebug_message_api.junebug_state_cache import (
    MemoryJunebugStateCache,
    TimeoutDict,
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


async def test_store_fetch_delete():
    """
    The cache must be able to store, fetch, and delete entries.
    """
    jsc = MemoryJunebugStateCache({})

    assert await jsc.fetch_event_http_info("foo") is None

    await jsc.store_event_http_info("foo", "http://localhost/blah", None)
    ehi = await jsc.fetch_event_http_info("foo")
    assert ehi is not None
    assert ehi.url == "http://localhost/blah"

    await jsc.delete_event_http_info("foo")
    assert await jsc.fetch_event_http_info("foo") is None


async def test_timeout(autojump_clock):
    """
    Entries disappear after the timeout period.
    """
    jsc = MemoryJunebugStateCache({"timeout": 60 * 60})

    await jsc.store_event_http_info("foo", "http://localhost/blah", None)
    assert await jsc.fetch_event_http_info("foo") is not None

    # Sleep until one second before the timeout.
    await trio.sleep(60 * 60 - 1)
    assert await jsc.fetch_event_http_info("foo") is not None

    # Sleep until one second after the timeout.
    await trio.sleep(2)
    assert await jsc.fetch_event_http_info("foo") is None
