import importlib.metadata

import pytest
import sentry_sdk
from trio import fail_after, open_memory_channel, sleep

from vumi2.messages import Event, EventType, Message, TransportType
from vumi2.workers import BaseWorker

# Since we're talking to a real AMQP broker in these tests, we can't rely on
# trio to let us know when all runnable tasks are done. That means we're stuck
# with timers to determine whether or not shutdown should have completed.
#  * CLOSED_WAIT_TIME is the baseline for how long we expect shutdown to take,
#    and we enforce that with a timeout so we'll notice if it's too short.
#  * UNCLOSED_WAIT_TIME is how long we wait before checking shutdowns that we
#    expect to be blocked, and it should be rather longer than a normal
#    shutdown to avoid thinking something's blocked when it's actually slow.
# The numbers here are an attempt to balance reliability (waiting longer) with
# convenience (finishing faster) and may need adjustment from time to time.
CLOSED_WAIT_TIME = 0.1
UNCLOSED_WAIT_TIME = 2.5 * CLOSED_WAIT_TIME


class FailingHealthcheckWorker(BaseWorker):
    async def setup(self):
        self.healthchecks["failing"] = self.failing_healthcheck

    async def failing_healthcheck(self):
        return {"health": "down"}


class AcloseWorker(BaseWorker):
    """
    A worker with a pair of connectors for use in shutdown/aclose tests.

    In order for tests to control message handling, each consumer sends
    its message/event to a memory channel (for the test to receive) and
    waits for a response on another channel.
    """

    async def setup(self):
        # Make sure we haven't added this to the base class since these tests
        # were written.
        assert not hasattr(self, "is_closed")
        self.is_closed = False
        self.s_consume_in, self.consume_in = open_memory_channel[Message](0)
        self.s_consume_ev, self.consume_ev = open_memory_channel[Event](0)
        self.s_consume_out, self.consume_out = open_memory_channel[Message](0)
        self.allow_in, self.r_allow_in = open_memory_channel[None](0)
        self.allow_ev, self.r_allow_ev = open_memory_channel[None](0)
        self.allow_out, self.r_allow_out = open_memory_channel[None](0)
        await self.setup_receive_inbound_connector("ri", self.handle_in, self.handle_ev)
        await self.setup_receive_outbound_connector("ro", self.handle_out)
        await self.start_consuming()

    async def aclose(self):
        await super().aclose()
        self.is_closed = True

    async def handle_in(self, message: Message):
        await self.s_consume_in.send(message)
        await self.r_allow_in.receive()

    async def handle_ev(self, event: Event):
        await self.s_consume_ev.send(event)
        await self.r_allow_ev.receive()

    async def handle_out(self, message: Message):
        await self.s_consume_out.send(message)
        await self.r_allow_out.receive()


class SlowSetupWorker(BaseWorker):
    """
    A worker whose connectors are set up with a delay between them.

    The RO handler sends to the RI publisher, which will fail if we process a
    message before all connectors are set up.
    """

    async def setup(self):
        self.s_exc, self.exc = open_memory_channel[Exception | None](1)
        await self.setup_receive_inbound_connector("ri", self.handle_in, self.handle_ev)
        await sleep(0.1)
        await self.setup_receive_outbound_connector("ro", self.handle_out)
        await self.start_consuming()

    async def handle_in(self, message: Message):
        # We catch and log exceptions in message handlers, so the only way the
        # test will know about them is if we tell it.
        try:
            await self.receive_outbound_connectors["ro"].publish_inbound(message)
            await self.s_exc.send(None)
        except Exception as e:
            await self.s_exc.send(e)
            raise

    async def handle_ev(self, event: Event):
        print("HANDLE EV!")

    async def handle_out(self, message: Message):
        print("HANDLE OUT!")


@pytest.fixture()
async def worker(worker_factory):
    config = {"http_bind": "localhost"}
    async with worker_factory.with_cleanup(BaseWorker, config) as worker:
        yield worker


def mkmsg(content: str) -> Message:
    return Message(
        to_addr="12345",
        from_addr="54321",
        transport_name="test",
        transport_type=TransportType.SMS,
        content=content,
    )


def mkev(msg_id: str) -> Event:
    return Event(
        user_message_id=msg_id,
        event_type=EventType.ACK,
        sent_message_id=msg_id,
    )


def get_sentry_client():
    """
    Fetch the current global sentry client for tests that manipulate sentry.
    """
    return sentry_sdk.get_global_scope().client


async def test_sentry_unconfigured(worker_factory):
    """
    When sentry_dsn isn't configured, sentry isn't set up.
    """
    assert not get_sentry_client().is_active()
    assert get_sentry_client().dsn is None
    worker_factory(BaseWorker, {})
    assert not get_sentry_client().is_active()
    assert get_sentry_client().dsn is None


async def test_sentry_configured(worker_factory):
    """
    When sentry_dsn is configured, sentry is set up at worker creation time.
    """
    assert not get_sentry_client().is_active()
    assert get_sentry_client().dsn is None
    try:
        worker_factory(BaseWorker, {"sentry_dsn": "http://key@example.org/0"})
        assert get_sentry_client().is_active()
        assert get_sentry_client().dsn == "http://key@example.org/0"
        version = importlib.metadata.distribution("vumi2").version
        assert get_sentry_client().options["release"] == version
    finally:
        # Disable sentry for the rest of the tests
        sentry_sdk.init()
    assert get_sentry_client().dsn is None


async def test_http_server_unconfigured(worker_factory):
    """
    When http_bind isn't configured, the worker http server isn't set up.
    """
    worker = worker_factory(BaseWorker, {})
    assert not hasattr(worker, "http")


async def test_http_server_configured(worker_factory):
    """
    When http_bind is configured, the worker http server is set up and
    endpoints may be configured.
    """
    worker = worker_factory(BaseWorker, {"http_bind": "localhost"})
    assert worker.http is not None
    worker.http.app.add_url_rule("/hi", view_func=lambda: ("hello", 200))
    response = await worker.http.app.test_client().get("/hi")
    assert await response.data == b"hello"


async def test_healthcheck(worker):
    client = worker.http.app.test_client()
    response = await client.get("/health")
    data = await response.json
    assert data["health"] == "ok"
    assert data["components"]["amqp"]["state"] == "open"


# The .with_args is to bypass pytest's special behaviour for callable marker args.
@pytest.mark.worker_class.with_args(FailingHealthcheckWorker)
async def test_down_healthcheck(worker):
    await worker.setup()
    client = worker.http.app.test_client()
    response = await client.get("/health")
    data = await response.json
    assert data["health"] == "down"
    assert data["components"]["failing"] == {"health": "down"}


@pytest.mark.worker_class.with_args(AcloseWorker)
async def test_aclose_idle(nursery, worker):
    """
    When a worker has no in-progress message handlers, clean shutdown
    has nothing to wait for.
    """
    await worker.setup()
    assert not worker.is_closed

    # We're talking to a real AMQP broker and thus need to wait for the network
    # and such, but shutdown should be quite quick. The timeout here is shorter
    # than wait time in later tests that check for shutdown being blocked.
    with fail_after(CLOSED_WAIT_TIME):
        await worker.aclose()

    # With nothing to block closing, we should now be closed.
    assert worker.is_closed


@pytest.mark.worker_class.with_args(AcloseWorker)
async def test_aclose_pending_inbound(nursery, worker, connector_factory):
    """
    When a worker is busy processing an inbound message, shutdown is
    blocked until the handler finishes.
    """
    ro_ri = await connector_factory.setup_ro("ri")
    await worker.setup()
    assert not worker.is_closed

    # Send a message and wait for it to reach the handler.
    await ro_ri.publish_inbound(mkmsg("hi"))
    assert (await worker.consume_in.receive()).content == "hi"

    # Start closing the worker and wait long enough to be confident that it
    # should already have finished before checking that it hasn't.
    nursery.start_soon(worker.aclose)
    await sleep(UNCLOSED_WAIT_TIME)
    assert not worker.is_closed

    # After we allow the handler to finish, shutdown can continue.
    await worker.allow_in.send(None)
    with fail_after(CLOSED_WAIT_TIME):
        await worker.aclose()
    assert worker.is_closed


@pytest.mark.worker_class.with_args(AcloseWorker)
async def test_aclose_pending_event(nursery, worker, connector_factory):
    """
    When a worker is busy processing an event, shutdown is blocked until
    the handler finishes.
    """
    ro_ri = await connector_factory.setup_ro("ri")
    await worker.setup()
    assert not worker.is_closed

    # Send an event and wait for it to reach the handler.
    await ro_ri.publish_event(mkev("1"))
    assert (await worker.consume_ev.receive()).user_message_id == "1"

    # Start closing the worker and wait long enough to be confident that it
    # should already have finished before checking that it hasn't.
    nursery.start_soon(worker.aclose)
    await sleep(UNCLOSED_WAIT_TIME)
    assert not worker.is_closed

    # After we allow the handler to finish, shutdown can continue.
    await worker.allow_ev.send(None)
    with fail_after(CLOSED_WAIT_TIME):
        await worker.aclose()
    assert worker.is_closed


@pytest.mark.worker_class.with_args(AcloseWorker)
async def test_aclose_pending_outbound(nursery, worker, connector_factory):
    """
    When a worker is busy processing an inbound message, shutdown is
    blocked until the handler finishes.
    """
    ri_ro = await connector_factory.setup_ri("ro")
    await worker.setup()
    assert not worker.is_closed

    # Send a message and wait for it to reach the handler.
    await ri_ro.publish_outbound(mkmsg("hi"))
    assert (await worker.consume_out.receive()).content == "hi"

    # Start closing the worker and wait long enough to be confident that it
    # should already have finished before checking that it hasn't.
    nursery.start_soon(worker.aclose)
    await sleep(UNCLOSED_WAIT_TIME)
    assert not worker.is_closed

    # After we allow the handler to finish, shutdown can complete.
    await worker.allow_out.send(None)
    with fail_after(CLOSED_WAIT_TIME):
        await worker.aclose()
    assert worker.is_closed


@pytest.mark.worker_class.with_args(SlowSetupWorker)
async def test_connector_setup_race(nursery, worker, connector_factory):
    """
    All connectors must be set up and available to publish before any messages
    are consumed.
    """
    # In order to send a message that the worker will receive at startup, we
    # need to create and bind the relevant queue. The easiest way to do that is
    # to create a a matching connector. We then close that connector so it
    # doesn't consume the message before the worker starts.
    ri_ri = await connector_factory.setup_ri("ri")
    await ri_ri.conn.aclose_consumers()

    ro_ri = await connector_factory.setup_ro("ri")
    ri_ro = await connector_factory.setup_ri("ro")

    # Publish a message before we start the worker so that the first connector
    # receives it as soon as it's able to receive.
    await ro_ri.publish_inbound(mkmsg("hi"))
    await sleep(0.1)

    await worker.setup()

    # If all went well, we get no exception and we receive the message through
    # the connector that was only set up later.
    assert (await worker.exc.receive()) is None
    assert (await ri_ro.consume_inbound()).content == "hi"


async def test_connector_setup_call_start_twice(connector_factory):
    """
    Starting connectors more than once should not cause the worker to hang or fail
    """
    ro_ri = await connector_factory.setup_ro("ri")
    await ro_ri.conn.start_consuming()


async def test_connector_setup_call_start_after_closing(connector_factory):
    """
    Starting connectors after closing the consumers should not cause the worker to
    hang or fail
    """
    ro_ri = await connector_factory.setup_ro("ri")
    await ro_ri.conn.aclose_consumers()
    await ro_ri.conn.start_consuming()
