import trio

from vumi2.applications.junebug_message_api.junebug_state_cache import (
    MemoryJunebugStateCache,
)
from vumi2.messages import Message


def mkmsg() -> Message:
    msg = {
        "to_addr": "to",
        "from_addr": "from",
        "transport_name": "tx",
        "transport_type": "sms",
    }
    return Message.deserialise(msg)


async def test_ehi_store_fetch_delete():
    """
    The cache must be able to store, fetch, and delete EventHttpInfo entries.
    """
    jsc = MemoryJunebugStateCache({})

    assert await jsc.fetch_event_http_info("foo") is None

    await jsc.store_event_http_info("foo", "http://localhost/blah", None)
    ehi = await jsc.fetch_event_http_info("foo")
    assert ehi is not None
    assert ehi.url == "http://localhost/blah"

    await jsc.delete_event_http_info("foo")
    assert await jsc.fetch_event_http_info("foo") is None


async def test_inbound_store_fetch_delete():
    """
    The cache must be able to store, fetch, and delete Message entries.
    """
    jsc = MemoryJunebugStateCache({})
    msg = mkmsg()

    assert await jsc.fetch_inbound(msg.message_id) is None

    await jsc.store_inbound(msg)
    msg2 = await jsc.fetch_inbound(msg.message_id)
    assert msg2 is not None
    assert msg2 == msg
    assert msg2 is not msg

    await jsc.delete_inbound(msg.message_id)
    assert await jsc.fetch_inbound(msg.message_id) is None


async def test_ehi_timeout(autojump_clock):
    """
    Entries disappear after the timeout period.
    """
    jsc = MemoryJunebugStateCache({"timeout": 60 * 60})
    msg = mkmsg()

    await jsc.store_event_http_info("foo", "http://localhost/blah", None)
    await jsc.store_inbound(msg)
    assert await jsc.fetch_event_http_info("foo") is not None
    assert await jsc.fetch_inbound(msg.message_id) is not None

    # Sleep until one second before the timeout.
    await trio.sleep(60 * 60 - 1)
    assert await jsc.fetch_event_http_info("foo") is not None
    assert await jsc.fetch_inbound(msg.message_id) is not None

    # Sleep until one second after the timeout.
    await trio.sleep(2)
    assert await jsc.fetch_event_http_info("foo") is None
    assert await jsc.fetch_inbound(msg.message_id) is None
