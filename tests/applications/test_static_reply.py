import pytest

from vumi2.applications.static_reply.static_reply import StaticReplyApplication
from vumi2.messages import Message, Session, TransportType

TEST_CONFIG = {"transport_name": "static_reply_test", "reply_text": "Test reply text"}


@pytest.fixture()
async def static_reply(worker_factory):
    async with worker_factory.with_cleanup(
        StaticReplyApplication, TEST_CONFIG
    ) as worker:
        yield worker


async def test_static_reply(static_reply, connector_factory):
    """
    The static reply app should reply to all inbounds with the configured reply text
    """
    await static_reply.setup()

    msg1 = Message(
        to_addr="12345",
        from_addr="54321",
        transport_name="test",
        transport_type=TransportType.USSD,
    )

    ro_test = await connector_factory.setup_ro(TEST_CONFIG["transport_name"])

    await ro_test.publish_inbound(msg1)

    reply = await ro_test.consume_outbound()

    assert reply.content == TEST_CONFIG["reply_text"]
    assert reply.session_event == Session.CLOSE
    assert reply.to_addr == msg1.from_addr
    assert reply.from_addr == msg1.to_addr
