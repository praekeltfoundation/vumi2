import logging
from base64 import b64encode
from contextlib import asynccontextmanager

import pytest
from attrs import define, field
from hypercorn import Config as HypercornConfig
from hypercorn.trio import serve as hypercorn_serve
from quart import request
from quart_trio import QuartTrio
from trio import fail_after, open_memory_channel, open_nursery
from trio.abc import ReceiveChannel, SendChannel
from werkzeug.datastructures import MultiDict

from vumi2.applications import JunebugMessageApi
from vumi2.messages import Event, EventType, Message, TransportType, generate_message_id


@define
class ReqInfo:
    method: str
    url: str
    path: str
    headers: dict[str, str]
    args: MultiDict[str, str]
    body_json: dict


@define
class RspInfo:
    code: int = 200
    body: str = ""
    disconnect: bool = False


@define
class HttpServer:
    app: QuartTrio
    bind: str

    _send_req: SendChannel[ReqInfo] = field(init=False)
    _recv_req: ReceiveChannel[ReqInfo] = field(init=False)
    _send_rsp: SendChannel[RspInfo] = field(init=False)
    _recv_rsp: ReceiveChannel[RspInfo] = field(init=False)

    def __attrs_post_init__(self):
        self._send_req, self._recv_req = open_memory_channel[ReqInfo](1)
        self._send_rsp, self._recv_rsp = open_memory_channel[RspInfo](1)
        self.app.add_url_rule(
            "/<path:path>", view_func=self._handle_req, methods=["POST"]
        )

    @classmethod
    async def start_new(cls, nursery):
        app = QuartTrio("test")
        cfg = HypercornConfig()
        cfg.bind = ["localhost:0"]
        [bind] = await nursery.start(hypercorn_serve, app, cfg)
        return cls(app=app, bind=bind)

    async def _handle_req(self, path):
        req = ReqInfo(
            method=request.method,
            url=request.url,
            path=path,
            headers=request.headers,
            args=request.args,
            body_json=await request.get_json(),
        )
        print(f"HTTP req: {req}")
        await self._send_req.send(req)
        rsp = await self._recv_rsp.receive()
        print(f"HTTP rsp: {rsp}")
        return rsp.body, rsp.code

    async def receive_req(self) -> ReqInfo:
        return await self._recv_req.receive()

    async def send_rsp(self, rsp: RspInfo):
        return await self._send_rsp.send(rsp)


@asynccontextmanager
async def handle_inbound(worker: JunebugMessageApi, msg: Message):
    async with open_nursery() as nursery:
        nursery.start_soon(worker.handle_inbound_message, msg)
        yield


@asynccontextmanager
async def handle_event(worker: JunebugMessageApi, ev: Event):
    async with open_nursery() as nursery:
        nursery.start_soon(worker.handle_event, ev)
        yield


@pytest.fixture()
async def http_server(nursery):
    return await HttpServer.start_new(nursery)


def mk_config(http_server: HttpServer, **config_update) -> dict:
    config = {
        "connector_name": "jma-test",
        "http_bind": "localhost:0",
        "mo_message_url": f"{http_server.bind}/message",
    }
    return {**config, **config_update}


@pytest.fixture()
async def jma_worker(worker_factory, http_server):
    config = mk_config(http_server)
    async with worker_factory.with_cleanup(JunebugMessageApi, config) as worker:
        await worker.setup()
        yield worker


@pytest.fixture()
async def jma_ro(connector_factory):
    return await connector_factory.setup_ro("jma-test")


def mkmsg(content: str, to_addr="123", from_addr="456") -> Message:
    return Message(
        to_addr=to_addr,
        from_addr=from_addr,
        transport_name="blah",
        transport_type=TransportType.SMS,
        message_id=generate_message_id(),
        content=content,
    )


def mkev(message_id: str, event_type: EventType, **fields) -> Event:
    if event_type == EventType.ACK:
        fields.setdefault("sent_message_id", message_id)
    return Event(
        user_message_id=message_id,
        event_type=event_type,
        **fields,
    )


async def test_inbound_message_amqp(jma_worker, jma_ro, http_server):
    """
    Inbound messages are forwarded to the configured URL.

    This test sends the inbound message over AMQP rather than calling
    the handler directly.
    """
    msg = mkmsg("hello")

    with fail_after(2):
        await jma_ro.publish_inbound(msg)
        req = await http_server.receive_req()
        await http_server.send_rsp(RspInfo())

    assert req.body_json["content"] == "hello"
    assert req.body_json["to"] == "123"


async def test_inbound_message(jma_worker, http_server):
    """
    Inbound messages are forwarded to the configured URL.

    This test calls the handler directly so we know when it's finished.
    """
    msg = mkmsg("hello")

    with fail_after(2):
        async with handle_inbound(jma_worker, msg):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.body_json["content"] == "hello"
    assert req.body_json["to"] == "123"


async def test_send_message_bad_response(jma_worker, http_server, caplog):
    """
    If an inbound message results in an HTTP error, the error and
    message are logged.
    """
    msg = mkmsg("hello")

    with fail_after(2):
        async with handle_inbound(jma_worker, msg):
            await http_server.receive_req()
            await http_server.send_rsp(RspInfo(code=500))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Error sending message, received HTTP code 500" in err.getMessage()


async def test_send_message_basic_auth_url(worker_factory, http_server):
    """
    If mo_message_url has credentials in it, those get sent as an
    Authorization header.
    """
    url = f"{http_server.bind}/message".replace("http://", "http://foo:bar@")
    config = mk_config(http_server, mo_message_url=url)

    msg = mkmsg("hello")

    async with worker_factory.with_cleanup(JunebugMessageApi, config) as jma_worker:
        await jma_worker.setup()
        with fail_after(2):
            async with handle_inbound(jma_worker, msg):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    basic = b64encode(b"foo:bar").decode()  # base64 is all bytes, not strs.
    assert req.headers["Authorization"] == f"Basic {basic}"


async def test_send_message_auth_token(worker_factory, http_server):
    """
    If mo_message_url has credentials in it, those get sent as an
    Authorization header.
    """
    token = "my-token"  # noqa: S105 (This is a fake token.)
    config = mk_config(http_server, mo_message_url_auth_token=token)

    msg = mkmsg("hello")

    async with worker_factory.with_cleanup(JunebugMessageApi, config) as jma_worker:
        await jma_worker.setup()
        with fail_after(2):
            async with handle_inbound(jma_worker, msg):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.headers["Authorization"] == "Token my-token"


async def test_event_not_message_id(jma_worker, http_server, caplog):
    """
    If we receive an event but don't have anything stored for the
    message_id it refers to, we log it and move on.
    """
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        await jma_worker.handle_event(ev)

    [warn] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert "Cannot find event URL, missing user_message_id" in warn.getMessage()


## Test cases from the original junebug codebase. The plan is to reimplement
## these for vumi2 as we add the associated functionality to JunebugMessageApi.

#     @inlineCallbacks
#     def test_forward_ack_http(self):
#         event = TransportEvent(
#             event_type='ack',
#             user_message_id='msg-21',
#             sent_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': self.url,
#                 'message_id': "msg-21",
#             })

#         yield self.worker.consume_ack(event)
#         [req] = self.logging_api.requests

#         self.assert_request(
#             req,
#             method='POST',
#             headers={'content-type': ['application/json']},
#             body=api_from_event(self.worker.channel_id, event))
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_ack_http_with_token_auth(self):
#         event = TransportEvent(
#             event_type='ack',
#             user_message_id='msg-21',
#             sent_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': '{}/auth/'.format(self.url),
#                 'event_auth_token': 'the-auth-token',
#                 'message_id': "msg-21",
#             })

#         yield self.worker.consume_ack(event)
#         [req] = self.logging_api.requests

#         self.assertEqual(req, {
#             'Authorization': ['Token the-auth-token']
#         })
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_ack_http_with_basic_auth(self):
#         event = TransportEvent(
#             event_type='ack',
#             user_message_id='msg-21',
#             sent_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')

#         # Inject the auth parameters
#         url = self.url.replace('http://', 'http://foo:bar@') + '/auth/'
#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': url,
#                 'message_id': "msg-21",
#             })

#         yield self.worker.consume_ack(event)
#         [req] = self.logging_api.requests

#         self.assertEqual(req, {
#             'Authorization': ['Basic %s' % (b64encode('foo:bar'),)]
#         })
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_ack_bad_response(self):
#         self.patch_logger()

#         event = TransportEvent(
#             event_type='ack',
#             user_message_id='msg-21',
#             sent_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': "{}/bad/".format(self.url),
#                 'message_id': "msg-21",
#             })

#         yield self.worker.consume_ack(event)

#         self.assert_was_logged(repr(event))
#         self.assert_was_logged('500')
#         self.assert_was_logged('test-error-response')
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_ack_no_message(self):
#         self.patch_logger()

#         event = TransportEvent(
#             event_type='ack',
#             user_message_id='msg-21',
#             sent_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.consume_ack(event)

#         self.assertEqual(self.logging_api.requests, [])
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_nack_http(self):
#         event = TransportEvent(
#             event_type='nack',
#             user_message_id='msg-21',
#             nack_reason='too many foos',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': self.url,
#                 'message_id': "msg-21",
#             })

#         yield self.worker.consume_nack(event)
#         [req] = self.logging_api.requests

#         self.assert_request(
#             req,
#             method='POST',
#             headers={'content-type': ['application/json']},
#             body=api_from_event(self.worker.channel_id, event))
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_nack_bad_response(self):
#         self.patch_logger()

#         event = TransportEvent(
#             event_type='nack',
#             user_message_id='msg-21',
#             nack_reason='too many foos',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': '{}/bad/'.format(self.url),
#                 'message_id': "msg-21",
#             })

#         yield self.worker.consume_nack(event)

#         self.assert_was_logged(repr(event))
#         self.assert_was_logged('500')
#         self.assert_was_logged('test-error-response')
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_nack_no_message(self):
#         self.patch_logger()

#         event = TransportEvent(
#             event_type='nack',
#             user_message_id='msg-21',
#             nack_reason='too many foos',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.consume_nack(event)

#         self.assertEqual(self.logging_api.requests, [])
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_dr_http(self):
#         event = TransportEvent(
#             event_type='delivery_report',
#             user_message_id='msg-21',
#             delivery_status='pending',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': self.url,
#                 'message_id': "msg-21",
#             })

#         yield self.worker.consume_delivery_report(event)
#         [req] = self.logging_api.requests

#         self.assert_request(
#             req,
#             method='POST',
#             headers={'content-type': ['application/json']},
#             body=api_from_event(self.worker.channel_id, event))
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_dr_bad_response(self):
#         self.patch_logger()

#         event = TransportEvent(
#             event_type='delivery_report',
#             user_message_id='msg-21',
#             delivery_status='pending',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': '{}/bad/'.format(self.url),
#                 'message_id': "msg-21",
#             })

#         yield self.worker.consume_delivery_report(event)

#         self.assert_was_logged(repr(event))
#         self.assert_was_logged('500')
#         self.assert_was_logged('test-error-response')
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_dr_no_message(self):
#         self.patch_logger()

#         event = TransportEvent(
#             event_type='delivery_report',
#             user_message_id='msg-21',
#             delivery_status='pending',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield self.worker.consume_delivery_report(event)

#         self.assertEqual(self.logging_api.requests, [])
#         yield self.assert_event_stored(event)

#     @inlineCallbacks
#     def test_forward_event_bad_event(self):
#         self.patch_logger()

#         event = TransportEvent(
#             event_type='ack',
#             user_message_id='msg-21',
#             sent_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')

#         event['event_type'] = 'bad'

#         yield self.worker.outbounds.store_message(
#             self.worker.channel_id, {
#                 'event_url': self.url,
#                 'message_id': "msg-21",
#             })

#         yield self.worker._forward_event(event)

#         self.assertEqual(self.logging_api.requests, [])
#         self.assert_was_logged("Discarding unrecognised event %r" % (event,))

#     @inlineCallbacks
#     def test_outbound_message_rates(self):
#         '''Outbound messages should increase the message send rates.'''
#         clock = self.patch_message_rate_clock()

#         worker = yield self.get_worker({
#             'message_rate_bucket': 1.0,
#         })

#         msg = TransportUserMessage.send(to_addr='+1234', content='testcontent')
#         yield worker.consume_user_message(msg)

#         clock.advance(1)

#         self.assertEqual((yield worker.message_rate.get_messages_per_second(
#             'testtransport', 'inbound', 1.0)), 1.0)

#     @inlineCallbacks
#     def test_submitted_event_rates(self):
#         '''Acknowledge events should increase the submitted event rates.'''
#         clock = self.patch_message_rate_clock()

#         worker = yield self.get_worker({
#             'message_rate_bucket': 1.0,
#         })

#         event = TransportEvent(
#             event_type='ack',
#             user_message_id='msg-21',
#             sent_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')
#         yield worker.consume_ack(event)

#         clock.advance(1)

#         self.assertEqual((yield worker.message_rate.get_messages_per_second(
#             'testtransport', 'submitted', 1.0)), 1.0)

#     @inlineCallbacks
#     def test_rejected_event_rates(self):
#         '''Not-acknowledge events should increase the rejected event rates.'''
#         clock = self.patch_message_rate_clock()

#         worker = yield self.get_worker({
#             'message_rate_bucket': 1.0,
#         })

#         event = TransportEvent(
#             event_type='nack',
#             nack_reason='bad message',
#             user_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')
#         yield worker.consume_nack(event)

#         clock.advance(1)

#         self.assertEqual((yield worker.message_rate.get_messages_per_second(
#             'testtransport', 'rejected', 1.0)), 1.0)

#     @inlineCallbacks
#     def test_delivery_succeeded_event_rates(self):
#         '''Delivered delivery reports should increase the delivery_succeeded
#         event rates.'''
#         clock = self.patch_message_rate_clock()

#         worker = yield self.get_worker({
#             'message_rate_bucket': 1.0,
#         })

#         event = TransportEvent(
#             event_type='delivery_report',
#             user_message_id='msg-21',
#             delivery_status='delivered',
#             timestamp='2015-09-22 15:39:44.827794')
#         yield worker.consume_delivery_report(event)

#         clock.advance(1)

#         self.assertEqual((yield worker.message_rate.get_messages_per_second(
#             'testtransport', 'delivery_succeeded', 1.0)), 1.0)

#     @inlineCallbacks
#     def test_delivery_failed_event_rates(self):
#         '''Failed delivery reports should increase the delivery_failed
#         event rates.'''
#         clock = self.patch_message_rate_clock()

#         worker = yield self.get_worker({
#             'message_rate_bucket': 1.0,
#         })

#         event = TransportEvent(
#             event_type='delivery_report',
#             user_message_id='msg-21',
#             delivery_status='failed',
#             timestamp='2015-09-22 15:39:44.827794')
#         yield worker.consume_delivery_report(event)

#         clock.advance(1)

#         self.assertEqual((yield worker.message_rate.get_messages_per_second(
#             'testtransport', 'delivery_failed', 1.0)), 1.0)

#     @inlineCallbacks
#     def test_delivery_pending_event_rates(self):
#         '''Pending delivery reports should increase the delivery_pending
#         event rates.'''
#         clock = self.patch_message_rate_clock()

#         worker = yield self.get_worker({
#             'message_rate_bucket': 1.0,
#         })

#         event = TransportEvent(
#             event_type='delivery_report',
#             user_message_id='msg-21',
#             delivery_status='pending',
#             timestamp='2015-09-22 15:39:44.827794')
#         yield worker.consume_delivery_report(event)

#         clock.advance(1)

#         self.assertEqual((yield worker.message_rate.get_messages_per_second(
#             'testtransport', 'delivery_pending', 1.0)), 1.0)

#     @inlineCallbacks
#     def test_teardown_without_startup(self):
#         '''If the teardown method is called before the worker was started up
#         correctly, the teardown should still succeed.'''
#         worker = yield self.get_worker(start=False)

#         self.assertEqual(getattr(worker, 'redis', None), None)

#         yield worker.teardown_application()

## Things we probably don't need anymore:

#     @inlineCallbacks
#     def setUp(self):
#         self.logging_api = RequestLoggingApi()
#         self.logging_api.setup()
#         self.addCleanup(self.logging_api.teardown)
#         self.url = self.logging_api.url

#         self.worker = yield self.get_worker()
#         connection_pool = HTTPConnectionPool(reactor, persistent=False)
#         treq._utils.set_global_pool(connection_pool)

#     @inlineCallbacks
#     def get_worker(self, config=None, start=True):
#         '''Get a new MessageForwardingWorker with the provided config'''
#         if config is None:
#             config = {}

#         self.app_helper = ApplicationHelper(MessageForwardingWorker)
#         yield self.app_helper.setup()
#         self.addCleanup(self.app_helper.cleanup)

#         persistencehelper = PersistenceHelper()
#         yield persistencehelper.setup()
#         self.addCleanup(persistencehelper.cleanup)

#         config = conjoin(persistencehelper.mk_config({
#             'transport_name': 'testtransport',
#             'mo_message_url': self.url.decode('utf-8'),
#             'inbound_ttl': 60,
#             'outbound_ttl': 60 * 60 * 24 * 2,
#             'metric_window': 1.0,
#         }), config)

#         worker = yield self.app_helper.get_application(config, start=start)
#         returnValue(worker)

#     @inlineCallbacks
#     def assert_event_stored(self, event):
#         key = '%s:outbound_messages:%s' % (
#             self.worker.config['transport_name'], 'msg-21')
#         event_json = yield self.worker.redis.hget(key, event['event_id'])
#         self.assertEqual(event_json, event.to_json())

#     @inlineCallbacks
#     def test_channel_id(self):
#         worker = yield self.get_worker({'transport_name': 'foo'})
#         self.assertEqual(worker.channel_id, 'foo')


## Test cases for features we may not want or need anymore.

## Message/event forwarding over AMQP. Do we actually use this anywhere?

#     @inlineCallbacks
#     def test_send_message_amqp(self):
#         '''A sent message should be forwarded to the correct AMQP queue if
#         the config option is set.'''
#         worker = yield self.get_worker(config={
#             'message_queue': 'testqueue'
#         })
#         msg = TransportUserMessage.send(to_addr='+1234', content='testcontent')
#         yield worker.consume_user_message(msg)

#         [dispatched_msg] = self.app_helper.get_dispatched(
#             'testqueue', 'inbound', TransportUserMessage)

#         self.assertEqual(dispatched_msg, msg)

#     @inlineCallbacks
#     def test_receive_message_amqp(self):
#         '''A received message on the configured queue should be forwarded to
#         the transport queue if the config option is set.'''
#         worker = yield self.get_worker(config={
#             'message_queue': 'testqueue'
#         })
#         msg = TransportUserMessage.send(to_addr='+1234', content='testcontent')
#         yield self.app_helper.dispatch_outbound(
#             msg, connector_name='testqueue')

#         [dispatched_msg] = yield self.app_helper.wait_for_dispatched_outbound(
#             connector_name=worker.transport_name)
#         self.assertEqual(dispatched_msg, msg)

#     @inlineCallbacks
#     def test_forward_ack_amqp(self):
#         '''A sent ack event should be forwarded to the correct AMQP queue if
#         the config option is set.'''
#         worker = yield self.get_worker(config={
#             'message_queue': 'testqueue'
#         })
#         event = TransportEvent(
#             event_type='ack',
#             user_message_id='msg-21',
#             sent_message_id='msg-21',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield worker.consume_ack(event)

#         [dispatched_msg] = self.app_helper.get_dispatched(
#             'testqueue', 'event', TransportEvent)

#         self.assertEqual(dispatched_msg['event_id'], event['event_id'])

#     @inlineCallbacks
#     def test_forward_nack_amqp(self):
#         '''A sent nack event should be forwarded to the correct AMQP queue if
#         the config option is set.'''
#         worker = yield self.get_worker(config={
#             'message_queue': 'testqueue'
#         })
#         event = TransportEvent(
#             event_type='nack',
#             user_message_id='msg-21',
#             nack_reason='too many foos',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield worker.consume_nack(event)

#         [dispatched_msg] = self.app_helper.get_dispatched(
#             'testqueue', 'event', TransportEvent)

#         self.assertEqual(dispatched_msg['event_id'], event['event_id'])

#     @inlineCallbacks
#     def test_forward_dr_amqp(self):
#         '''A sent delivery report event should be forwarded to the correct
#         AMQP queue if the config option is set.'''
#         worker = yield self.get_worker(config={
#             'message_queue': 'testqueue'
#         })
#         event = TransportEvent(
#             event_type='delivery_report',
#             user_message_id='msg-21',
#             delivery_status='pending',
#             timestamp='2015-09-22 15:39:44.827794')

#         yield worker.consume_nack(event)

#         [dispatched_msg] = self.app_helper.get_dispatched(
#             'testqueue', 'event', TransportEvent)

#         self.assertEqual(dispatched_msg['event_id'], event['event_id'])

## Storing inbound messages. As far as I can tell, we only use the message
## store for event routing. We don't need to store inbound events for that.

#     @inlineCallbacks
#     def test_send_message_storing(self):
#         '''Inbound messages should be stored in the InboundMessageStore'''
#         msg = TransportUserMessage.send(to_addr='+1234', content='testcontent')
#         yield self.worker.consume_user_message(msg)

#         redis = self.worker.redis
#         key = '%s:inbound_messages:%s' % (
#             self.worker.config['transport_name'], msg['message_id'])
#         msg_json = yield redis.hget(key, 'message')
#         self.assertEqual(TransportUserMessage.from_json(msg_json), msg)

## I'm not sure how we'd close the connection to test. Worth doing if we can, though.

#     @inlineCallbacks
#     def test_send_message_imploding_response(self):
#         '''If there is an error connecting to the configured URL, the
#         error and message should be logged'''
#         self.patch_logger()
#         self.worker = yield self.get_worker({
#             'transport_name': 'testtransport',
#             'mo_message_url': self.url + '/implode/',
#             })
#         msg = TransportUserMessage.send(to_addr='+1234', content='testcontent')
#         yield self.worker.consume_user_message(msg)

#         self.assert_was_logged('Post to %s/implode/ failed because of' % (
#             self.url,))
#         self.assert_was_logged('ConnectionDone')
