import json
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
from vumi2.applications.junebug_message_api.junebug_state_cache import EventHttpInfo
from vumi2.messages import (
    DeliveryStatus,
    Event,
    EventType,
    Message,
    TransportType,
    generate_message_id,
)


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


async def store_inbound(worker: JunebugMessageApi, msg: Message):
    await worker.state_cache.store_inbound(msg)


async def fetch_inbound(worker: JunebugMessageApi, message_id: str):
    return await worker.state_cache.fetch_inbound(message_id)


async def store_ehi(worker: JunebugMessageApi, message_id, url, auth_token):
    await worker.state_cache.store_event_http_info(message_id, url, auth_token)


async def fetch_ehi(worker: JunebugMessageApi, message_id):
    return await worker.state_cache.fetch_event_http_info(message_id)


@asynccontextmanager
async def handle_event(worker: JunebugMessageApi, ev: Event):
    async with open_nursery() as nursery:
        nursery.start_soon(worker.handle_event, ev)
        yield


async def post_outbound(
    worker: JunebugMessageApi, msg_dict: dict, path="/messages"
) -> bytes:
    client = worker.http.app.test_client()
    headers = {"Content-Type": "application/json"}
    async with client.request(path=path, method="POST", headers=headers) as connection:
        await connection.send(json.dumps(msg_dict).encode())
        await connection.send_complete()
        return await connection.receive()


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
        transport_name="jma-test",
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


def mkoutbound(content: str, to="+1234", from_addr="+23456", **kw) -> dict:
    return {"content": content, "to": to, "from": from_addr, **kw}


def mkreply(content: str, reply_to: str, **kw) -> dict:
    return {"content": content, "reply_to": reply_to, **kw}


def parse_send_message_response(resp: bytes) -> dict:
    rjson = json.loads(resp)
    [rjson["result"].pop(key) for key in ["message_id", "timestamp"]]
    return rjson


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

    assert req.path == "message"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["content"] == "hello"
    assert req.body_json["to"] == "123"

    assert await fetch_inbound(jma_worker, msg.message_id) == msg


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

    assert await fetch_inbound(jma_worker, msg.message_id) == msg


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

    assert await fetch_inbound(jma_worker, msg.message_id) == msg


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
    If mo_message_url_auth_token is set, we use its token in the
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


async def test_event_no_message_id(jma_worker, http_server, caplog):
    """
    If we receive an event but don't have anything stored for the
    message_id it refers to, we log it and move on.
    """
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        await jma_worker.handle_event(ev)

    [warn] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert "Cannot find event URL, missing user_message_id" in warn.getMessage()


async def test_forward_ack_amqp(jma_worker, jma_ro, http_server):
    """
    An ack event referencing an outbound message we know about is
    forwarded over HTTP.

    This test sends the inbound message over AMQP rather than calling
    the handler directly.
    """
    await store_ehi(jma_worker, "msg-21", f"{http_server.bind}/event", None)
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        await jma_ro.publish_event(ev)
        req = await http_server.receive_req()
        await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["event_type"] == "submitted"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "jma-test"


async def test_forward_ack(jma_worker, http_server):
    """
    An ack event referencing an outbound message we know about is
    forwarded over HTTP.
    """
    await store_ehi(jma_worker, "msg-21", f"{http_server.bind}/event", None)
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        async with handle_event(jma_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["event_type"] == "submitted"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "jma-test"


async def test_forward_ack_basic_auth_url(jma_worker, http_server):
    """
    If an event's URL has credentials in it, we use them for basic auth
    when forwarding over HTTP.
    """
    url = f"{http_server.bind}/event".replace("http://", "http://foo:bar@")
    await store_ehi(jma_worker, "msg-21", url, None)
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        async with handle_event(jma_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    basic = b64encode(b"foo:bar").decode()  # base64 is all bytes, not strs.
    assert req.headers["Authorization"] == f"Basic {basic}"
    assert req.body_json["event_type"] == "submitted"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "jma-test"


async def test_forward_ack_auth_token(jma_worker, http_server):
    """
    If an event has an auth token associated with it, we use that when
    forwarding over HTTP.
    """
    url = f"{http_server.bind}/event"
    await store_ehi(jma_worker, "msg-21", url, "my-event-token")
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        async with handle_event(jma_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["Authorization"] == "Token my-event-token"
    assert req.body_json["event_type"] == "submitted"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "jma-test"


async def test_forward_ack_bad_response(jma_worker, http_server, caplog):
    """
    If forwarding an ack results in an HTTP error, the error and event
    are logged.
    """
    await store_ehi(jma_worker, "msg-21", f"{http_server.bind}/event", None)
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        async with handle_event(jma_worker, ev):
            await http_server.receive_req()
            await http_server.send_rsp(RspInfo(code=500))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Error sending event, received HTTP code 500" in err.getMessage()


async def test_forward_nack(jma_worker, http_server):
    """
    A nack event referencing an outbound message we know about is
    forwarded over HTTP.
    """
    await store_ehi(jma_worker, "msg-21", f"{http_server.bind}/event", None)
    ev = mkev("msg-21", EventType.NACK, nack_reason="KaBooM!")

    with fail_after(2):
        async with handle_event(jma_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["event_type"] == "rejected"
    assert req.body_json["event_details"] == {"reason": "KaBooM!"}
    assert req.body_json["channel_id"] == "jma-test"


async def test_forward_dr(jma_worker, http_server):
    """
    A delivery report event referencing an outbound message we know
    about is forwarded over HTTP.
    """
    await store_ehi(jma_worker, "m-21", f"{http_server.bind}/event", None)
    ev = mkev("m-21", EventType.DELIVERY_REPORT, delivery_status=DeliveryStatus.PENDING)

    with fail_after(2):
        async with handle_event(jma_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["event_type"] == "delivery_pending"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "jma-test"


async def test_send_outbound(jma_worker, jma_ro):
    """
    An outbound message received over HTTP is forwarded over AMQP.

    We don't store anything if event_url is unset.
    """
    body = mkoutbound("foo")
    with fail_after(2):
        response = await post_outbound(jma_worker, body)
        outbound = await jma_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "to": "+1234",
            "from": "+23456",
            "group": None,
            "reply_to": None,
            "channel_id": "jma-test",
            "channel_data": {},
            "content": "foo",
        },
    }

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "+23456"
    assert outbound.group is None
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "jma-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"

    assert await fetch_ehi(jma_worker, outbound.message_id) is None


async def test_send_outbound_event_url(jma_worker, jma_ro, http_server):
    """
    An outbound message with event_url set stores event info.
    """
    body = mkoutbound("foo", event_url=f"{http_server.bind}/event")
    with fail_after(2):
        response = await post_outbound(jma_worker, body)
        outbound = await jma_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "to": "+1234",
            "from": "+23456",
            "group": None,
            "reply_to": None,
            "channel_id": "jma-test",
            "channel_data": {},
            "content": "foo",
        },
    }

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "+23456"
    assert outbound.group is None
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "jma-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"

    ehi = await fetch_ehi(jma_worker, outbound.message_id)
    assert ehi == EventHttpInfo(f"{http_server.bind}/event", None)


async def test_send_outbound_event_auth(jma_worker, jma_ro, http_server):
    """
    An outbound message with event_url and event_auth_token set stores event info.
    """
    token = "token"  # noqa: S105 (This is a fake token.)
    body = mkoutbound(
        "foo", event_url=f"{http_server.bind}/event", event_auth_token=token
    )
    with fail_after(2):
        response = await post_outbound(jma_worker, body)
        outbound = await jma_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "to": "+1234",
            "from": "+23456",
            "group": None,
            "reply_to": None,
            "channel_id": "jma-test",
            "channel_data": {},
            "content": "foo",
        },
    }

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "+23456"
    assert outbound.group is None
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "jma-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"

    ehi = await fetch_ehi(jma_worker, outbound.message_id)
    assert ehi == EventHttpInfo(f"{http_server.bind}/event", "token")


async def test_send_outbound_invalid_json(jma_worker):
    """
    An attempted send with a non-json body returns an appropriate error.
    """
    with fail_after(2):
        client = jma_worker.http.app.test_client()
        async with client.request(path="/messages", method="POST") as connection:
            await connection.send(b"gimme r00t?")
            await connection.send_complete()
            response = await connection.receive()

    expected_err = {
        "message": "Expecting value: line 1 column 1 (char 0)",
        "type": "JsonDecodeError",
    }
    assert json.loads(response) == {
        "status": 400,
        "code": "Bad Request",
        "description": "json decode error",
        "result": {"errors": [expected_err]},
    }


async def test_send_outbound_group(jma_worker, jma_ro):
    """
    An outbound group message received over HTTP is forwarded over AMQP.
    """
    body = mkoutbound("foo", group="my-group")
    with fail_after(2):
        response = await post_outbound(jma_worker, body)
        outbound = await jma_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "to": "+1234",
            "from": "+23456",
            "group": "my-group",
            "reply_to": None,
            "channel_id": "jma-test",
            "channel_data": {},
            "content": "foo",
        },
    }

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "+23456"
    assert outbound.group == "my-group"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "jma-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"


async def test_send_outbound_reply(jma_worker, jma_ro):
    """
    An outbound reply message received over HTTP is forwarded over AMQP
    if the original message can be found.
    """
    inbound = mkmsg("inbound")
    await store_inbound(jma_worker, inbound)
    body = mkreply("foo", reply_to=inbound.message_id)
    with fail_after(2):
        response = await post_outbound(jma_worker, body)
        outbound = await jma_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "to": "456",
            "from": "123",
            "group": None,
            "reply_to": inbound.message_id,
            "channel_id": "jma-test",
            "channel_data": {"session_event": "resume"},
            "content": "foo",
        },
    }

    assert outbound.to_addr == "456"
    assert outbound.from_addr == "123"
    assert outbound.group is None
    assert outbound.in_reply_to == inbound.message_id
    assert outbound.transport_name == "jma-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"


async def test_send_outbound_reply_no_stored_inbound(jma_worker):
    """
    An outbound reply message received over HTTP cannot be forwarded
    over AMQP if the original message cannot be found.
    """
    body = mkreply("foo", reply_to="no-such-message")
    with fail_after(2):
        response = await post_outbound(jma_worker, body)

    expected_err = {
        "message": "Inbound message with id no-such-message not found",
        "type": "MessageNotFound",
    }
    assert json.loads(response) == {
        "status": 400,
        "code": "Bad Request",
        "description": "message not found",
        "result": {"errors": [expected_err]},
    }


async def test_send_outbound_no_to_or_reply(jma_worker, jma_ro):
    """
    An outbound reply message received over HTTP is forwarded over AMQP
    if the original message can be found.
    """
    with fail_after(2):
        response = await post_outbound(jma_worker, {"content": "going nowhere"})

    expected_err = {
        "message": 'Either "to" or "reply_to" must be specified',
        "type": "ApiUsageError",
    }
    assert json.loads(response) == {
        "status": 400,
        "code": "Bad Request",
        "description": "api usage error",
        "result": {"errors": [expected_err]},
    }


async def test_send_outbound_extra_field(jma_worker, jma_ro):
    """
    An outbound reply message received over HTTP is forwarded over AMQP
    if the original message can be found.
    """
    body = mkoutbound("too much", foo="extra")
    with fail_after(2):
        response = await post_outbound(jma_worker, body)

    expected_err = {
        "message": "Additional properties are not allowed (u'foo' was unexpected)",
        "type": "invalid_body",
    }
    assert json.loads(response) == {
        "status": 400,
        "code": "Bad Request",
        "description": "api usage error",
        "result": {"errors": [expected_err]},
    }


## Test cases from the original junebug api codebase.

#     @inlineCallbacks
#     def test_send_message_both_to_and_reply_to_allowing_expiry(self):
#         properties = self.create_channel_properties(character_limit=100)
#         config = yield self.create_channel_config(
#             allow_expired_replies=True)
#         redis = yield self.get_redis()
#         yield self.stop_server()
#         yield self.start_server(config=config)

#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)

#         resp = yield self.post('/channels/test-channel/messages/', {
#             'from': None,
#             'to': '+1234',
#             'reply_to': '2e8u9ua8',
#             'content': 'foo',
#         })
#         yield self.assert_response(
#             resp, http.CREATED, 'message submitted', {
#                 'channel_data': {},
#                 'from': None,
#                 'to': '+1234',
#                 'content': 'foo',
#                 'group': None,
#                 'channel_id': u'test-channel',
#                 'reply_to': None,
#             }, ignore=['timestamp', 'message_id'])

#     @inlineCallbacks
#     def test_send_message_from_and_reply_to(self):
#         properties = self.create_channel_properties(character_limit=100)
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)

#         resp = yield self.post('/channels/test-channel/messages/', {
#             'from': None,
#             'to': '+1234',
#             'reply_to': '2e8u9ua8',
#             'content': None,
#         })
#         yield self.assert_response(
#             resp, http.BAD_REQUEST, 'message not found', {
#                 'errors': [{
#                     'message': 'Inbound message with id 2e8u9ua8 not found',
#                     'type': 'MessageNotFound',
#                 }]
#             })

#     @inlineCallbacks
#     def test_send_message_under_character_limit(self):
#         '''If the content length is under the character limit, no errors should
#         be returned'''
#         properties = self.create_channel_properties(character_limit=100)
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)
#         resp = yield self.post('/channels/test-channel/messages/', {
#             'to': '+1234', 'content': 'Under the character limit.',
#             'from': None})
#         yield self.assert_response(
#             resp, http.CREATED, 'message submitted', {
#                 'to': '+1234',
#                 'channel_id': 'test-channel',
#                 'from': None,
#                 'group': None,
#                 'reply_to': None,
#                 'channel_data': {},
#                 'content': 'Under the character limit.',
#             }, ignore=['timestamp', 'message_id'])

#     @inlineCallbacks
#     def test_send_message_equal_character_limit(self):
#         '''If the content length is equal to the character limit, no errors
#         should be returned'''
#         content = 'Equal to the character limit.'
#         properties = self.create_channel_properties(
#             character_limit=len(content))
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)
#         resp = yield self.post('/channels/test-channel/messages/', {
#             'to': '+1234', 'content': content, 'from': None})
#         yield self.assert_response(
#             resp, http.CREATED, 'message submitted', {
#                 'to': '+1234',
#                 'channel_id': 'test-channel',
#                 'from': None,
#                 'group': None,
#                 'reply_to': None,
#                 'channel_data': {},
#                 'content': content,
#             }, ignore=['timestamp', 'message_id'])

#     @inlineCallbacks
#     def test_send_message_over_character_limit(self):
#         '''If the content length is over the character limit, an error should
#         be returned'''
#         properties = self.create_channel_properties(character_limit=10)
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)
#         resp = yield self.post('/channels/test-channel/messages/', {
#             'to': '+1234', 'content': 'Over the character limit.',
#             'from': None})
#         yield self.assert_response(
#             resp, http.BAD_REQUEST, 'message too long', {
#                 'errors': [{
#                     'message':
#                         "Message content u'Over the character limit.' "
#                         "is of length 25, which is greater than the character "
#                         "limit of 10",
#                     'type': 'MessageTooLong',
#                 }],
#             })

#     @inlineCallbacks
#     def test_get_message_status_no_events(self):
#         '''Returns `None` for last event fields, and empty list for events'''

#         properties = self.create_channel_properties(character_limit=10)
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)

#         resp = yield self.get(
#             '/channels/{}/messages/message-id'.format(channel.id))
#         yield self.assert_response(
#             resp, http.OK, 'message status', {
#                 'id': 'message-id',
#                 'last_event_type': None,
#                 'last_event_timestamp': None,
#                 'events': [],
#             })

#     @inlineCallbacks
#     def test_get_message_status_with_no_destination(self):
#         '''Returns error if the channel has no destination'''
#         properties = self.create_channel_properties(character_limit=10)
#         del properties['mo_url']
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)

#         resp = yield self.get(
#             '/channels/{}/messages/message-id'.format(channel.id))

#         yield self.assert_response(
#             resp, http.BAD_REQUEST, 'api usage error', {
#                 'errors': [{
#                     'message': 'This channel has no "mo_url" or "amqp_queue"',
#                     'type': 'ApiUsageError',
#                 }]
#             })

#     @inlineCallbacks
#     def test_get_message_status_one_event(self):
#         '''Returns the event details for last event fields, and list with
#         single event for `events`'''

#         properties = self.create_channel_properties(character_limit=10)
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)

#         event = TransportEvent(
#             user_message_id='message-id', sent_message_id='message-id',
#             event_type='nack', nack_reason='error error')
#         yield self.outbounds.store_event(channel.id, 'message-id', event)
#         resp = yield self.get(
#             '/channels/{}/messages/message-id'.format(channel.id))
#         event_dict = api_from_event(channel.id, event)
#         event_dict['timestamp'] = str(event_dict['timestamp'])
#         yield self.assert_response(
#             resp, http.OK, 'message status', {
#                 'id': 'message-id',
#                 'last_event_type': 'rejected',
#                 'last_event_timestamp': str(event['timestamp']),
#                 'events': [event_dict],
#             })

#     @inlineCallbacks
#     def test_get_message_status_multiple_events(self):
#         '''Returns the last event details for last event fields, and list with
#         all events for `events`'''

#         properties = self.create_channel_properties(character_limit=10)
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)

#         events = []
#         event_dicts = []
#         for i in range(5):
#             event = TransportEvent(
#                 user_message_id='message-id', sent_message_id='message-id',
#                 event_type='nack', nack_reason='error error')
#             yield self.outbounds.store_event(channel.id, 'message-id', event)
#             events.append(event)
#             event_dict = api_from_event(channel.id, event)
#             event_dict['timestamp'] = str(event_dict['timestamp'])
#             event_dicts.append(event_dict)

#         resp = yield self.get(
#             '/channels/{}/messages/message-id'.format(channel.id))
#         yield self.assert_response(
#             resp, http.OK, 'message status', {
#                 'id': 'message-id',
#                 'last_event_type': 'rejected',
#                 'last_event_timestamp': event_dicts[-1]['timestamp'],
#                 'events': event_dicts,
#             })

#     @inlineCallbacks
#     def test_get_health_check(self):
#         resp = yield self.get('/health')
#         yield self.assert_response(
#             resp, http.OK, 'health ok', {})

#     @inlineCallbacks
#     def test_get_channels_health_check(self):

#         config = yield self.create_channel_config(
#             rabbitmq_management_interface="rabbitmq:15672"
#         )
#         yield self.stop_server()
#         yield self.start_server(config=config)

#         channel = yield self.create_channel(self.service, self.redis)

#         request_list = []

#         for sub in ['inbound', 'outbound', 'event']:
#             queue_name = "%s.%s" % (channel.id, sub)
#             url = 'http://rabbitmq:15672/api/queues/%%2F/%s' % (queue_name)
#             request_list.append(
#                 ((b'get', url, mock.ANY, mock.ANY, mock.ANY),
#                  (http.OK, {b'Content-Type': b'application/json'},
#                   b'{"messages": 1256, "message_stats": {"ack_details": {"rate": 1.25}}, "name": "%s"}' % queue_name)))  # noqa

#         async_failures = []
#         sequence_stubs = RequestSequence(request_list, async_failures.append)
#         stub_treq = StubTreq(StringStubbingResource(sequence_stubs))

#         def new_get(*args, **kwargs):
#             return stub_treq.request("GET", args[0])

#         with (mock.patch('treq.client.HTTPClient.get', side_effect=new_get)):
#             with sequence_stubs.consume(self.fail):
#                 resp = yield self.request('GET', '/health')

#             yield self.assertEqual(async_failures, [])
#             yield self.assert_response(
#                 resp, http.OK, 'queues ok', [
#                     {
#                         'messages': 1256,
#                         'name': '%s.inbound' % (channel.id),
#                         'rate': 1.25,
#                         'stuck': False
#                     }, {
#                         'messages': 1256,
#                         'name': '%s.outbound' % (channel.id),
#                         'rate': 1.25,
#                         'stuck': False
#                     }, {
#                         'messages': 1256,
#                         'name': '%s.event' % (channel.id),
#                         'rate': 1.25,
#                         'stuck': False
#                     }])

#     @inlineCallbacks
#     def test_get_channels_and_destinations_health_check(self):

#         config = yield self.create_channel_config(
#             rabbitmq_management_interface="rabbitmq:15672"
#         )
#         yield self.stop_server()
#         yield self.start_server(config=config)

#         channel = yield self.create_channel(self.service, self.redis)

#         config = self.create_router_config()
#         router = Router(self.api, config)
#         dest_config = self.create_destination_config()
#         destination = router.add_destination(dest_config)
#         dest_config = self.create_destination_config()
#         destination2 = router.add_destination(dest_config)
#         router.save()

#         destinations = sorted([destination.id, destination2.id])

#         request_list = []
#         response = []

#         for queue_id in [channel.id] + destinations:
#             for sub in ['inbound', 'outbound', 'event']:
#                 queue_name = "%s.%s" % (queue_id, sub)
#                 url = 'http://rabbitmq:15672/api/queues/%%2F/%s' % (queue_name)
#                 request_list.append(
#                     ((b'get', url, mock.ANY, mock.ANY, mock.ANY),
#                      (http.OK, {b'Content-Type': b'application/json'},
#                       b'{"messages": 1256, "message_stats": {"ack_details": {"rate": 1.25}}, "name": "%s"}' % queue_name)))  # noqa

#                 response.append({
#                     'messages': 1256,
#                     'name': '{}.{}'.format(queue_id, sub),
#                     'rate': 1.25,
#                     'stuck': False
#                 })

#         async_failures = []
#         sequence_stubs = RequestSequence(request_list, async_failures.append)
#         stub_treq = StubTreq(StringStubbingResource(sequence_stubs))

#         def new_get(*args, **kwargs):
#             return stub_treq.request("GET", args[0])

#         with (mock.patch('treq.client.HTTPClient.get', side_effect=new_get)):
#             with sequence_stubs.consume(self.fail):
#                 resp = yield self.request('GET', '/health')

#             yield self.assertEqual(async_failures, [])
#             yield self.assert_response(resp, http.OK, 'queues ok', response)

#     @inlineCallbacks
#     def test_get_channels_health_check_stuck(self):

#         config = yield self.create_channel_config(
#             rabbitmq_management_interface="rabbitmq:15672"
#         )
#         yield self.stop_server()
#         yield self.start_server(config=config)

#         channel = yield self.create_channel(self.service, self.redis)

#         request_list = []

#         for sub in ['inbound', 'outbound', 'event']:
#             queue_name = "%s.%s" % (channel.id, sub)
#             url = 'http://rabbitmq:15672/api/queues/%%2F/%s' % (queue_name)
#             request_list.append(
#                 ((b'get', url, mock.ANY, mock.ANY, mock.ANY),
#                  (http.OK, {b'Content-Type': b'application/json'},
#                   b'{"messages": 1256, "message_stats": {"ack_details": {"rate": 0.0}}, "name": "%s"}' % queue_name)))  # noqa

#         async_failures = []
#         sequence_stubs = RequestSequence(request_list, async_failures.append)
#         stub_treq = StubTreq(StringStubbingResource(sequence_stubs))

#         def new_get(*args, **kwargs):
#             return stub_treq.request("GET", args[0])

#         with (mock.patch('treq.client.HTTPClient.get', side_effect=new_get)):
#             with sequence_stubs.consume(self.fail):
#                 resp = yield self.request('GET', '/health')

#             yield self.assertEqual(async_failures, [])
#             yield self.assert_response(
#                 resp, http.INTERNAL_SERVER_ERROR, 'queues stuck', [
#                     {
#                         'messages': 1256,
#                         'name': '%s.inbound' % (channel.id),
#                         'rate': 0,
#                         'stuck': True
#                     }, {
#                         'messages': 1256,
#                         'name': '%s.outbound' % (channel.id),
#                         'rate': 0,
#                         'stuck': True
#                     }, {
#                         'messages': 1256,
#                         'name': '%s.event' % (channel.id),
#                         'rate': 0,
#                         'stuck': True
#                     }])

#     @inlineCallbacks
#     def test_get_channels_health_check_stuck_no_message_stats(self):

#         config = yield self.create_channel_config(
#             rabbitmq_management_interface="rabbitmq:15672"
#         )
#         yield self.stop_server()
#         yield self.start_server(config=config)

#         channel = yield self.create_channel(self.service, self.redis)

#         request_list = []

#         for sub in ['inbound', 'outbound', 'event']:
#             queue_name = "%s.%s" % (channel.id, sub)
#             url = 'http://rabbitmq:15672/api/queues/%%2F/%s' % (queue_name)
#             request_list.append(
#                 ((b'get', url, mock.ANY, mock.ANY, mock.ANY),
#                  (http.OK, {b'Content-Type': b'application/json'},
#                   b'{"messages": 1256, "messages_details": {"rate": 0.0}, "name": "%s"}' % queue_name)))  # noqa

#         async_failures = []
#         sequence_stubs = RequestSequence(request_list, async_failures.append)
#         stub_treq = StubTreq(StringStubbingResource(sequence_stubs))

#         def new_get(*args, **kwargs):
#             return stub_treq.request("GET", args[0])

#         with (mock.patch('treq.client.HTTPClient.get', side_effect=new_get)):
#             with sequence_stubs.consume(self.fail):
#                 resp = yield self.request('GET', '/health')

#             yield self.assertEqual(async_failures, [])
#             yield self.assert_response(
#                 resp, http.OK, 'queues ok', [
#                     {
#                         'messages': 1256,
#                         'name': '%s.inbound' % (channel.id),
#                         'stuck': False
#                     }, {
#                         'messages': 1256,
#                         'name': '%s.outbound' % (channel.id),
#                         'stuck': False
#                     }, {
#                         'messages': 1256,
#                         'name': '%s.event' % (channel.id),
#                         'stuck': False
#                     }])

#     @inlineCallbacks
#     def test_get_channel_logs_no_logs(self):
#         '''If there are no logs, an empty list should be returned.'''
#         channel = yield self.create_channel(self.service, self.redis)
#         log_worker = channel.transport_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         yield log_worker.startService()
#         resp = yield self.get('/channels/%s/logs' % channel.id, params={
#             'n': '3',
#         })
#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [])

#     @inlineCallbacks
#     def test_get_channel_logs_less_than_limit(self):
#         '''If the amount of logs is less than the limit, all the logs should
#         be returned.'''
#         channel = yield self.create_channel(
#             self.service, self.redis,
#             'junebug.tests.helpers.LoggingTestTransport')
#         worker_logger = channel.transport_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         worker_logger.startService()

#         channel.transport_worker.test_log('Test')
#         resp = yield self.get('/channels/%s/logs' % channel.id, params={
#             'n': '2',
#         })
#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [], ignore=[0])
#         [log] = (yield resp.json())['result']
#         self.assert_log(log, {
#             'logger': channel.id,
#             'message': 'Test',
#             'level': logging.INFO})

#     @inlineCallbacks
#     def test_get_channel_logs_more_than_limit(self):
#         '''If the amount of logs is more than the limit, only the latest n
#         should be returned.'''
#         channel = yield self.create_channel(
#             self.service, self.redis,
#             'junebug.tests.helpers.LoggingTestTransport')
#         worker_logger = channel.transport_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         worker_logger.startService()

#         channel.transport_worker.test_log('Test1')
#         channel.transport_worker.test_log('Test2')
#         channel.transport_worker.test_log('Test3')
#         resp = yield self.get('/channels/%s/logs' % channel.id, params={
#             'n': '2',
#         })
#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [], ignore=[1, 0])
#         [log1, log2] = (yield resp.json())['result']
#         self.assert_log(log1, {
#             'logger': channel.id,
#             'message': 'Test3',
#             'level': logging.INFO})
#         self.assert_log(log2, {
#             'logger': channel.id,
#             'message': 'Test2',
#             'level': logging.INFO})

#     @inlineCallbacks
#     def test_get_channel_logs_more_than_configured(self):
#         '''If the amount of requested logs is more than what is
#         configured, then only the configured amount of logs are returned.'''
#         logpath = self.mktemp()
#         config = yield self.create_channel_config(
#             max_logs=2,
#             channels={
#                 'logging': 'junebug.tests.helpers.LoggingTestTransport',
#             },
#             logging_path=logpath
#         )
#         properties = yield self.create_channel_properties(type='logging')
#         yield self.stop_server()
#         yield self.start_server(config=config)
#         channel = yield self.create_channel(
#             self.service, self.redis, config=config, properties=properties)
#         worker_logger = channel.transport_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         worker_logger.startService()

#         channel.transport_worker.test_log('Test1')
#         channel.transport_worker.test_log('Test2')
#         channel.transport_worker.test_log('Test3')
#         resp = yield self.get('/channels/%s/logs' % channel.id, params={
#             'n': '3',
#         })

#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [], ignore=[1, 0])
#         [log1, log2] = (yield resp.json())['result']
#         self.assert_log(log1, {
#             'logger': channel.id,
#             'message': 'Test3',
#             'level': logging.INFO})
#         self.assert_log(log2, {
#             'logger': channel.id,
#             'message': 'Test2',
#             'level': logging.INFO})

#     @inlineCallbacks
#     def test_get_channel_logs_no_n(self):
#         '''If the number of logs is not specified, then the API should return
#         the configured maximum number of logs.'''
#         logpath = self.mktemp()
#         config = yield self.create_channel_config(
#             max_logs=2,
#             channels={
#                 'logging': 'junebug.tests.helpers.LoggingTestTransport',
#             },
#             logging_path=logpath
#         )
#         properties = yield self.create_channel_properties(type='logging')
#         yield self.stop_server()
#         yield self.start_server(config=config)
#         channel = yield self.create_channel(
#             self.service, self.redis, config=config, properties=properties)
#         worker_logger = channel.transport_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         worker_logger.startService()

#         channel.transport_worker.test_log('Test1')
#         channel.transport_worker.test_log('Test2')
#         channel.transport_worker.test_log('Test3')
#         resp = yield self.get('/channels/%s/logs' % channel.id)

#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [], ignore=[1, 0])
#         [log1, log2] = (yield resp.json())['result']
#         self.assert_log(log1, {
#             'logger': channel.id,
#             'message': 'Test3',
#             'level': logging.INFO})
#         self.assert_log(log2, {
#             'logger': channel.id,
#             'message': 'Test2',
#             'level': logging.INFO})

#     @inlineCallbacks
#     def test_get_router_list(self):
#         '''A GET request on the routers collection endpoint should result in
#         the list of router UUIDs being returned'''
#         redis = yield self.get_redis()

#         resp = yield self.get('/routers/')
#         yield self.assert_response(resp, http.OK, 'routers retrieved', [])

#         yield redis.sadd('routers', '64f78582-8e83-40c9-be23-cc93d54e9dcd')

#         resp = yield self.get('/routers/')
#         yield self.assert_response(resp, http.OK, 'routers retrieved', [
#             u'64f78582-8e83-40c9-be23-cc93d54e9dcd',
#         ])

#         yield redis.sadd('routers', 'ceee6a83-fa6b-42d2-b65f-1a1cf85ac6f8')

#         resp = yield self.get('/routers/')
#         yield self.assert_response(resp, http.OK, 'routers retrieved', [
#             u'64f78582-8e83-40c9-be23-cc93d54e9dcd',
#             u'ceee6a83-fa6b-42d2-b65f-1a1cf85ac6f8',
#         ])

#     @inlineCallbacks
#     def test_create_router(self):
#         """Creating a router with a valid config should succeed"""
#         config = self.create_router_config()
#         resp = yield self.post('/routers/', config)

#         yield self.assert_response(
#             resp, http.CREATED, 'router created', config, ignore=['id'])

#     @inlineCallbacks
#     def test_create_router_invalid_worker_config(self):
#         """The worker config should be sent to the router for validation"""
#         config = self.create_router_config(config={'test': 'fail'})
#         resp = yield self.post('/routers/', config)

#         yield self.assert_response(
#             resp, http.BAD_REQUEST, 'invalid router config', {
#                 'errors': [{
#                     'message': 'test must be pass',
#                     'type': 'InvalidRouterConfig',
#                 }]
#             })

#     @inlineCallbacks
#     def test_create_router_worker(self):
#         """When creating a new router, the router worker should successfully
#         be started"""
#         config = self.create_router_config()
#         resp = yield self.post('/routers/', config)

#         # Check that the worker is created with the correct config
#         id = (yield resp.json())['result']['id']
#         transport = self.service.namedServices[id]

#         self.assertEqual(transport.parent, self.service)

#         worker_config = config['config']
#         for k, v in worker_config.items():
#             self.assertEqual(transport.config[k], worker_config[k])

#     @inlineCallbacks
#     def test_create_router_saves_config(self):
#         """When creating a worker, the config should be saved inside the router
#         store"""
#         config = self.create_router_config()
#         resp = yield self.post('/routers/', config)

#         routers = yield self.api.router_store.get_router_list()
#         self.assertEqual(routers, [(yield resp.json())['result']['id']])

#     @inlineCallbacks
#     def test_get_router(self):
#         """The get router endpoint should return the config and status of the
#         specified router"""
#         config = self.create_router_config()
#         resp = yield self.post('/routers/', config)

#         router_id = (yield resp.json())['result']['id']
#         resp = yield self.get('/routers/{}'.format(router_id))
#         self.assert_response(
#             resp, http.OK, 'router found', config, ignore=['id'])

#     @inlineCallbacks
#     def test_get_non_existing_router(self):
#         """If a router for the given ID does not exist, then a not found error
#         should be returned"""
#         resp = yield self.get('/routers/bad-router-id')
#         self.assert_response(resp, http.NOT_FOUND, 'router not found', {
#             'errors': [{
#                 'message': 'Router with ID bad-router-id cannot be found',
#                 'type': 'RouterNotFound',
#             }]
#         })

#     @inlineCallbacks
#     def test_replace_router_config(self):
#         """When creating a PUT request, the router configuration should be
#         replaced"""
#         old_config = self.create_router_config(label='test', config={
#             'test': 'pass', 'foo': 'bar'})
#         resp = yield self.post('/routers/', old_config)
#         router_id = (yield resp.json())['result']['id']

#         router_config = yield self.api.router_store.get_router_config(
#             router_id)
#         old_config['id'] = router_id
#         self.assertEqual(router_config, old_config)
#         router_worker = self.api.service.namedServices[router_id]
#         router_worker_config = old_config['config']
#         for k, v in router_worker_config.items():
#             self.assertEqual(router_worker.config[k], router_worker_config[k])

#         new_config = self.create_router_config(config={'test': 'pass'})
#         new_config.pop('label', None)
#         resp = yield self.put('/routers/{}'.format(router_id), new_config)
#         new_config['id'] = router_id

#         yield self.assert_response(
#             resp, http.OK, 'router updated', new_config)

#         router_config = yield self.api.router_store.get_router_config(
#             router_id)
#         self.assertEqual(router_config, new_config)
#         router_worker = self.api.service.namedServices[router_id]
#         router_worker_config = new_config['config']
#         for k, v in router_worker_config.items():
#             self.assertEqual(router_worker.config[k], router_worker_config[k])

#         router_worker = self.api.service.namedServices[router_id]

#     @inlineCallbacks
#     def test_replace_router_config_invalid_worker_config(self):
#         """Before replacing the worker config, the new config should be
#         validated."""
#         old_config = self.create_router_config(config={'test': 'pass'})
#         resp = yield self.post('/routers/', old_config)
#         router_id = (yield resp.json())['result']['id']

#         new_config = self.create_router_config(config={'test': 'fail'})
#         resp = yield self.put('/routers/{}'.format(router_id), new_config)

#         yield self.assert_response(
#             resp, http.BAD_REQUEST, 'invalid router config', {
#                 'errors': [{
#                     'message': 'test must be pass',
#                     'type': 'InvalidRouterConfig',
#                 }]
#             })

#         resp = yield self.get('/routers/{}'.format(router_id))
#         yield self.assert_response(
#             resp, http.OK, 'router found', old_config, ignore=['id'])

#     @inlineCallbacks
#     def test_update_router_config(self):
#         """When creating a PATCH request, the router configuration should be
#         updated"""
#         old_config = self.create_router_config(
#             label='old', config={'test': 'pass'})
#         resp = yield self.post('/routers/', old_config)
#         router_id = (yield resp.json())['result']['id']

#         router_config = yield self.api.router_store.get_router_config(
#             router_id)
#         old_config['id'] = router_id
#         self.assertEqual(router_config, old_config)
#         router_worker = self.api.service.namedServices[router_id]
#         router_worker_config = old_config['config']
#         for k, v in router_worker_config.items():
#             self.assertEqual(router_worker.config[k], router_worker_config[k])

#         update = {'config': {'test': 'pass', 'new': 'new'}}
#         new_config = deepcopy(old_config)
#         new_config.update(update)
#         self.assertEqual(new_config['label'], 'old')
#         resp = yield self.patch_request(
#             '/routers/{}'.format(router_id), update)

#         yield self.assert_response(
#             resp, http.OK, 'router updated', new_config)

#         router_config = yield self.api.router_store.get_router_config(
#             router_id)
#         self.assertEqual(router_config, new_config)
#         router_worker = self.api.service.namedServices[router_id]
#         router_worker_config = new_config['config']
#         for k, v in router_worker_config.items():
#             self.assertEqual(router_worker.config[k], router_worker_config[k])

#         router_worker = self.api.service.namedServices[router_id]

#     @inlineCallbacks
#     def test_update_router_config_invalid_worker_config(self):
#         """Before updating the worker config, the new config should be
#         validated."""
#         old_config = self.create_router_config(config={'test': 'pass'})
#         resp = yield self.post('/routers/', old_config)
#         router_id = (yield resp.json())['result']['id']

#         update = {'config': {'test': 'fail'}}
#         resp = yield self.patch_request(
#             '/routers/{}'.format(router_id), update)

#         yield self.assert_response(
#             resp, http.BAD_REQUEST, 'invalid router config', {
#                 'errors': [{
#                     'message': 'test must be pass',
#                     'type': 'InvalidRouterConfig',
#                 }]
#             })

#         resp = yield self.get('/routers/{}'.format(router_id))
#         yield self.assert_response(
#             resp, http.OK, 'router found', old_config, ignore=['id'])

#     @inlineCallbacks
#     def test_update_from_address_router_config(self):
#         """When creating a PATCH request, the from address router configuration
#         should be updated"""

#         resp = yield self.post('/channels/', {
#             'type': 'telnet',
#             'config': {
#                 'twisted_endpoint': 'tcp:0',
#             }
#         })
#         channel_id = (yield resp.json())['result']['id']

#         old_config = self.create_router_config(
#             label='old', type='from_address',
#             config={'channel': channel_id})
#         resp = yield self.post('/routers/', old_config)
#         router_id = (yield resp.json())['result']['id']

#         update = {'config': {'channel': channel_id}}
#         new_config = deepcopy(old_config)
#         new_config.update(update)
#         resp = yield self.patch_request(
#             '/routers/{}'.format(router_id), new_config)

#         yield self.assert_response(
#             resp, http.OK, 'router updated', new_config, ignore=['id'])

#     @inlineCallbacks
#     def test_delete_router(self):
#         """Should stop the router from running, and delete its config"""
#         config = self.create_router_config()
#         resp = yield self.post('/routers/', config)
#         router_id = (yield resp.json())['result']['id']

#         self.assertTrue(router_id in self.service.namedServices)
#         routers = yield self.api.router_store.get_router_list()
#         self.assertEqual(routers, [router_id])

#         resp = yield self.delete('/routers/{}'.format(router_id))
#         self.assert_response(resp, http.OK, 'router deleted', {})
#         self.assertFalse(router_id in self.service.namedServices)
#         routers = yield self.api.router_store.get_router_list()
#         self.assertEqual(routers, [])

#     @inlineCallbacks
#     def test_delete_non_existing_router(self):
#         """Should return a router not found"""
#         resp = yield self.delete('/routers/bad-id')
#         self.assert_response(resp, http.NOT_FOUND, 'router not found', {
#             'errors': [{
#                 'message': 'Router with ID bad-id cannot be found',
#                 'type': 'RouterNotFound',
#             }]
#         })

#     @inlineCallbacks
#     def test_get_router_logs_no_logs(self):
#         '''If there are no logs, an empty list should be returned.'''
#         router = yield self.create_test_router(self.service)
#         log_worker = router.router_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         yield log_worker.startService()
#         resp = yield self.get('/routers/%s/logs' % router.id, params={
#             'n': '3',
#         })
#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [])

#     @inlineCallbacks
#     def test_get_router_logs_less_than_limit(self):
#         '''If the amount of logs is less than the limit, all the logs should
#         be returned.'''
#         router = yield self.create_test_router(self.service)
#         worker_logger = router.router_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         worker_logger.startService()

#         router.router_worker.test_log('Test')
#         resp = yield self.get('/routers/%s/logs' % router.id, params={
#             'n': '2',
#         })
#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [], ignore=[0])
#         [log] = (yield resp.json())['result']
#         self.assert_log(log, {
#             'logger': router.id,
#             'message': 'Test',
#             'level': logging.INFO})

#     @inlineCallbacks
#     def test_get_router_logs_more_than_limit(self):
#         '''If the amount of logs is more than the limit, only the latest n
#         should be returned.'''
#         router = yield self.create_test_router(self.service)
#         worker_logger = router.router_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         worker_logger.startService()

#         router.router_worker.test_log('Test1')
#         router.router_worker.test_log('Test2')
#         router.router_worker.test_log('Test3')
#         resp = yield self.get('/routers/%s/logs' % router.id, params={
#             'n': '2',
#         })
#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [], ignore=[1, 0])
#         [log1, log2] = (yield resp.json())['result']
#         self.assert_log(log1, {
#             'logger': router.id,
#             'message': 'Test3',
#             'level': logging.INFO})
#         self.assert_log(log2, {
#             'logger': router.id,
#             'message': 'Test2',
#             'level': logging.INFO})

#     @inlineCallbacks
#     def test_get_router_logs_more_than_configured(self):
#         '''If the amount of requested logs is more than what is
#         configured, then only the configured amount of logs are returned.'''
#         logpath = self.mktemp()
#         config = yield self.create_channel_config(
#             max_logs=2,
#             logging_path=logpath
#         )
#         yield self.stop_server()
#         yield self.start_server(config=config)
#         router = yield self.create_test_router(self.service)
#         worker_logger = router.router_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         worker_logger.startService()

#         router.router_worker.test_log('Test1')
#         router.router_worker.test_log('Test2')
#         router.router_worker.test_log('Test3')
#         resp = yield self.get('/routers/%s/logs' % router.id, params={
#             'n': '3',
#         })

#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [], ignore=[1, 0])
#         [log1, log2] = (yield resp.json())['result']
#         self.assert_log(log1, {
#             'logger': router.id,
#             'message': 'Test3',
#             'level': logging.INFO})
#         self.assert_log(log2, {
#             'logger': router.id,
#             'message': 'Test2',
#             'level': logging.INFO})

#     @inlineCallbacks
#     def test_get_router_logs_no_n(self):
#         '''If the number of logs is not specified, then the API should return
#         the configured maximum number of logs.'''
#         logpath = self.mktemp()
#         config = yield self.create_channel_config(
#             max_logs=2,
#             logging_path=logpath
#         )
#         yield self.stop_server()
#         yield self.start_server(config=config)
#         router = yield self.create_test_router(self.service)
#         worker_logger = router.router_worker.getServiceNamed(
#             'Junebug Worker Logger')
#         worker_logger.startService()

#         router.router_worker.test_log('Test1')
#         router.router_worker.test_log('Test2')
#         router.router_worker.test_log('Test3')
#         resp = yield self.get('/routers/%s/logs' % router.id)

#         self.assert_response(
#             resp, http.OK, 'logs retrieved', [], ignore=[1, 0])
#         [log1, log2] = (yield resp.json())['result']
#         self.assert_log(log1, {
#             'logger': router.id,
#             'message': 'Test3',
#             'level': logging.INFO})
#         self.assert_log(log2, {
#             'logger': router.id,
#             'message': 'Test2',
#             'level': logging.INFO})

#     @inlineCallbacks
#     def test_create_destination_invalid_router_id(self):
#         """If the router specified by the router ID doesn't exist, a not found
#         error should be returned"""
#         resp = yield self.post('/routers/bad-router-id/destinations/', {
#             'config': {}
#         })
#         self.assert_response(resp, http.NOT_FOUND, 'router not found', {
#             'errors': [{
#                 'message': 'Router with ID bad-router-id cannot be found',
#                 'type': 'RouterNotFound',
#             }]
#         })

#     @inlineCallbacks
#     def test_create_destination_invalid_config(self):
#         """The destination config should be sent to the router worker to
#         validate before the destination is created"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(config={
#             'target': 'invalid',
#         })
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         self.assert_response(
#             resp, http.BAD_REQUEST, 'invalid router destination config', {
#                 'errors': [{
#                     'message': 'target must be valid',
#                     'type': 'InvalidRouterDestinationConfig',
#                 }]
#             }
#         )

#     @inlineCallbacks
#     def test_create_destination(self):
#         """A created destination should be saved in the router store, and be
#         passed to the router worker config"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(
#             label='testlabel',
#             metadata={'test': 'metadata'},
#             mo_url='http//example.org',
#             mo_url_token='12345',
#             amqp_queue='testqueue',
#             character_limit=7,
#         )
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         self.assert_response(
#             resp, http.CREATED, 'destination created', dest_config,
#             ignore=['id'])
#         dest_id = (yield resp.json())['result']['id']

#         self.assertEqual(
#             (yield self.api.router_store.get_router_destination_list(
#                 router_id)),
#             [dest_id])

#         dest_config['id'] = dest_id
#         router_worker = self.api.service.namedServices[router_id]
#         self.assertEqual(router_worker.config['destinations'], [dest_config])

#     @inlineCallbacks
#     def test_get_destination_list_non_existing_router(self):
#         """If we try to get a destination list for a router that doesn't
#         exist, we should get a not found error returned"""
#         resp = yield self.get('/routers/bad-router-id/destinations/')
#         self.assert_response(
#             resp, http.NOT_FOUND, 'router not found', {
#                 'errors': [{
#                     'message': 'Router with ID bad-router-id cannot be found',
#                     'type': 'RouterNotFound',
#                 }]
#             })

#     @inlineCallbacks
#     def test_get_destination_list(self):
#         """A GET request on the destinations resource should return a list of
#         destinations for that router"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config()
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         dest_id = (yield resp.json())['result']['id']

#         resp = yield self.get('/routers/{}/destinations/'.format(router_id))
#         self.assert_response(
#             resp, http.OK, 'destinations retrieved', [dest_id])

#     @inlineCallbacks
#     def test_get_destination_no_router(self):
#         """Trying to get a destination for a router that doesn't exist should
#         result in a not found error being returned"""
#         resp = yield self.get(
#             '/routers/bad-router-id/destinations/bad-destination-id')
#         self.assert_response(resp, http.NOT_FOUND, 'router not found', {
#             'errors': [{
#                 'message': 'Router with ID bad-router-id cannot be found',
#                 'type': 'RouterNotFound',
#             }]
#         })

#     @inlineCallbacks
#     def test_get_destination_no_destination(self):
#         """Trying to get a destination that doesn't exist should result in a
#         not found error being returned"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         resp = yield self.get(
#             '/routers/{}/destinations/bad-destination-id'.format(router_id))
#         self.assert_response(resp, http.NOT_FOUND, 'destination not found', {
#             'errors': [{
#                 'message':
#                     'Cannot find destination with ID bad-destination-id for '
#                     'router {}'.format(router_id),
#                 'type': 'DestinationNotFound',
#             }]
#         })

#     @inlineCallbacks
#     def test_get_destination(self):
#         """A GET request on a destination should return the status and config
#         of that destination"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         destination_config = self.create_destination_config()
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), destination_config)
#         destination_id = (yield resp.json())['result']['id']

#         resp = yield self.get(
#             '/routers/{}/destinations/{}'.format(router_id, destination_id))
#         self.assert_response(
#             resp, http.OK, 'destination found', destination_config,
#             ignore=['id'])

#     @inlineCallbacks
#     def test_replace_destination_config(self):
#         """A put request should replace the config of the destination, and save
#         that change."""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(label='testlabel')
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         self.assert_response(
#             resp, http.CREATED, 'destination created', dest_config,
#             ignore=['id'])
#         destination_id = (yield resp.json())['result']['id']
#         router_worker = self.api.service.namedServices[router_id]
#         destination = router_worker.config['destinations'][0]
#         self.assertIn('label', destination)

#         new_config = self.create_destination_config(character_limit=7)
#         resp = yield self.put(
#             '/routers/{}/destinations/{}'.format(router_id, destination_id),
#             new_config)
#         self.assert_response(
#             resp, http.OK, 'destination updated', new_config,
#             ignore=['id'])

#         router_worker = self.api.service.namedServices[router_id]
#         destination = router_worker.config['destinations'][0]
#         self.assertNotIn('label', destination)

#     @inlineCallbacks
#     def test_replace_destination_config_invalid_config(self):
#         """If there's an error in the provided config, an error should be
#         returned and the config should not be updated"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config()
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         new_config = self.create_destination_config(
#             config={'target': 'invalid'})
#         resp = yield self.put(
#             '/routers/{}/destinations/{}'.format(router_id, destination_id),
#             new_config)
#         self.assert_response(
#             resp, http.BAD_REQUEST, 'invalid router destination config', {
#                 'errors': [{
#                     'message': 'target must be valid',
#                     'type': 'InvalidRouterDestinationConfig',
#                 }],
#             })

#         router_worker = self.api.service.namedServices[router_id]
#         destination = router_worker.config['destinations'][0]
#         self.assertEqual(destination['config'], dest_config['config'])

#     @inlineCallbacks
#     def test_replace_destination_config_non_existing_destination(self):
#         """If the destination doesn't exist, then a not found error should be
#         returned"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config()
#         resp = yield self.put(
#             '/routers/{}/destinations/bad-id'.format(router_id), dest_config)
#         self.assert_response(
#             resp, http.NOT_FOUND, 'destination not found', {
#                 'errors': [{
#                     'message':
#                         'Cannot find destination with ID bad-id for router '
#                         '{}'.format(router_id),
#                     'type': 'DestinationNotFound',
#                 }],
#             })

#     @inlineCallbacks
#     def test_update_destination_config(self):
#         """A patch request should replace the config of the destination, and
#         save that change."""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(label='testlabel')
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         self.assert_response(
#             resp, http.CREATED, 'destination created', dest_config,
#             ignore=['id'])
#         destination_id = (yield resp.json())['result']['id']

#         resp = yield self.patch_request(
#             '/routers/{}/destinations/{}'.format(router_id, destination_id),
#             {'metadata': {'foo': 'bar'}, 'character_limit': 7})
#         self.assert_response(
#             resp, http.OK, 'destination updated', dest_config,
#             ignore=['id', 'metadata', 'character_limit'])

#         router_worker = self.api.service.namedServices[router_id]
#         destination = router_worker.config['destinations'][0]
#         self.assertEqual(destination['label'], 'testlabel')
#         self.assertEqual(destination['metadata'], {'foo': 'bar'})

#     @inlineCallbacks
#     def test_update_destination_config_invalid_config(self):
#         """If there's an error in the provided config, an error should be
#         returned and the config should not be updated"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config()
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         new_config = self.create_destination_config(
#             config={'target': 'invalid'})
#         resp = yield self.patch_request(
#             '/routers/{}/destinations/{}'.format(router_id, destination_id),
#             new_config)
#         self.assert_response(
#             resp, http.BAD_REQUEST, 'invalid router destination config', {
#                 'errors': [{
#                     'message': 'target must be valid',
#                     'type': 'InvalidRouterDestinationConfig',
#                 }],
#             })

#         router_worker = self.api.service.namedServices[router_id]
#         destination = router_worker.config['destinations'][0]
#         self.assertEqual(destination['config'], dest_config['config'])

#     @inlineCallbacks
#     def test_update_destination_config_non_existing_destination(self):
#         """If the destination doesn't exist, then a not found error should be
#         returned"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config()
#         resp = yield self.patch_request(
#             '/routers/{}/destinations/bad-id'.format(router_id), dest_config)
#         self.assert_response(
#             resp, http.NOT_FOUND, 'destination not found', {
#                 'errors': [{
#                     'message':
#                         'Cannot find destination with ID bad-id for router '
#                         '{}'.format(router_id),
#                     'type': 'DestinationNotFound',
#                 }],
#             })

#     @inlineCallbacks
#     def test_delete_destination(self):
#         """A DELETE request on a destination should remove that destination
#         from the import router config"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config()
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         router_worker = self.api.service.namedServices[router_id]
#         self.assertEqual(len(router_worker.config['destinations']), 1)

#         resp = yield self.delete(
#             '/routers/{}/destinations/{}'.format(router_id, destination_id))
#         self.assert_response(resp, http.OK, 'destination deleted', {})

#         router_worker = self.api.service.namedServices[router_id]
#         self.assertEqual(len(router_worker.config['destinations']), 0)

#     @inlineCallbacks
#     def test_delete_non_existing_destination(self):
#         """If the destination doesn't exist, then a not found error should be
#         returned"""
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         resp = yield self.delete(
#             '/routers/{}/destinations/bad-destination'.format(router_id))
#         self.assert_response(
#             resp, http.NOT_FOUND, 'destination not found', {
#                 'errors': [{
#                     'message':
#                         "Cannot find destination with ID bad-destination for "
#                         "router {}".format(router_id),
#                     'type': "DestinationNotFound",
#                 }]
#             })

#     @inlineCallbacks
#     def test_send_destination_message_invalid_router(self):
#         resp = yield self.post(
#             '/routers/foo-bar/destinations/test-destination/messages/',
#             {'to': '+1234', 'from': '', 'content': None})
#         yield self.assert_response(
#             resp, http.NOT_FOUND, 'router not found', {
#                 'errors': [{
#                     'message': 'Router with ID foo-bar cannot be found',
#                     'type': 'RouterNotFound',
#                     }]
#                 })

#     @inlineCallbacks
#     def test_send_destination_message_invalid_destination(self):
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         resp = yield self.post(
#             '/routers/{}/destinations/test-destination/messages/'.format(
#                 router_id),
#             {'to': '+1234', 'from': '', 'content': None})
#         yield self.assert_response(
#             resp, http.NOT_FOUND, 'destination not found', {
#                 'errors': [{
#                     'message':
#                         'Cannot find destination with ID test-destination for '
#                         'router {}'.format(router_id),
#                     'type': 'DestinationNotFound',
#                     }]
#                 })

#     @inlineCallbacks
#     def test_send_destination_message(self):
#         '''Sending a message should place the message on the queue for the
#         channel'''
#         properties = self.create_channel_properties()
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)

#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(
#             config={'channel': 'test-channel'})
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         resp = yield self.post(
#             '/routers/{}/destinations/{}/messages/'.format(
#                 router_id, destination_id),
#             {'to': '+1234', 'content': 'foo', 'from': None})
#         yield self.assert_response(
#             resp, http.CREATED, 'message submitted', {
#                 'to': '+1234',
#                 'channel_id': 'test-channel',
#                 'from': None,
#                 'group': None,
#                 'reply_to': None,
#                 'channel_data': {},
#                 'content': 'foo',
#             }, ignore=['timestamp', 'message_id'])

#         [message] = self.get_dispatched_messages('test-channel.outbound')
#         message_id = (yield resp.json())['result']['message_id']
#         self.assertEqual(message['message_id'], message_id)

#         event_url = yield self.api.outbounds.load_event_url(
#             'test-channel', message['message_id'])
#         self.assertEqual(event_url, None)

#     @inlineCallbacks
#     def test_send_destination_message_reply(self):
#         '''Sending a reply message should fetch the relevant inbound message,
#         use it to construct a reply message, and place the reply message on the
#         queue for the channel'''
#         properties = self.create_channel_properties()
#         config = yield self.create_channel_config()
#         redis = yield self.get_redis()
#         channel = Channel(redis, config, properties, id='test-channel')
#         yield channel.save()
#         yield channel.start(self.service)

#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(
#             config={'channel': 'test-channel'})
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         in_msg = TransportUserMessage(
#             from_addr='+2789',
#             to_addr='+1234',
#             transport_name='test-channel',
#             transport_type='_',
#             transport_metadata={'foo': 'bar'})

#         yield self.api.inbounds.store_vumi_message(destination_id, in_msg)
#         expected = in_msg.reply(content='testcontent')
#         expected = api_from_message(expected)

#         resp = yield self.post(
#             '/routers/{}/destinations/{}/messages/'.format(
#                 router_id, destination_id),
#             {'reply_to': in_msg['message_id'], 'content': 'testcontent'})

#         yield self.assert_response(
#             resp, http.CREATED,
#             'message submitted',
#             omit(expected, 'timestamp', 'message_id'),
#             ignore=['timestamp', 'message_id'])

#         [message] = self.get_dispatched_messages('test-channel.outbound')
#         message_id = (yield resp.json())['result']['message_id']
#         self.assertEqual(message['message_id'], message_id)

#         event_url = yield self.api.outbounds.load_event_url(
#             'test-channel', message['message_id'])
#         self.assertEqual(event_url, None)

#         # router_worker = self.api.service.namedServices[router_id]
#         stored_message = yield self.api.outbounds.load_message(
#             destination_id, message['message_id'])

#         self.assertEqual(api_from_message(message), stored_message)

#     @inlineCallbacks
#     def test_get_destination_message_invalid_router(self):
#         resp = yield self.get(
#             '/routers/foo-bar/destinations/test-destination/messages/message-id')  # noqa
#         yield self.assert_response(
#             resp, http.NOT_FOUND, 'router not found', {
#                 'errors': [{
#                     'message': 'Router with ID foo-bar cannot be found',
#                     'type': 'RouterNotFound',
#                     }]
#                 })

#     @inlineCallbacks
#     def test_get_destination_message_invalid_destination(self):
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         resp = yield self.get(
#             '/routers/{}/destinations/test-destination/messages/message-id'.format(  # noqa
#                 router_id))
#         yield self.assert_response(
#             resp, http.NOT_FOUND, 'destination not found', {
#                 'errors': [{
#                     'message':
#                         'Cannot find destination with ID test-destination for '
#                         'router {}'.format(router_id),
#                     'type': 'DestinationNotFound',
#                     }]
#                 })

#     @inlineCallbacks
#     def test_get_destination_message_status_no_events(self):
#         '''Returns `None` for last event fields, and empty list for events'''
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(config={'channel': '123'})
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         resp = yield self.get(
#             '/routers/{}/destinations/{}/messages/message-id'.format(
#                 router_id, destination_id))
#         yield self.assert_response(
#             resp, http.OK, 'message status', {
#                 'id': 'message-id',
#                 'last_event_type': None,
#                 'last_event_timestamp': None,
#                 'events': [],
#             })

#     @inlineCallbacks
#     def test_get_destination_message_status_with_events(self):
#         '''Returns `None` for last event fields, and empty list for events'''
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(
#             config={'channel': 'channel-id'})
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         resp = yield self.get(
#             '/routers/{}/destinations/{}/messages/message-id'.format(
#                 router_id, destination_id))
#         yield self.assert_response(
#             resp, http.OK, 'message status', {
#                 'id': 'message-id',
#                 'last_event_type': None,
#                 'last_event_timestamp': None,
#                 'events': [],
#             })

#     @inlineCallbacks
#     def test_get_destination_message_status_one_event(self):
#         '''Returns the event details for last event fields, and list with
#         single event for `events`'''
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(
#             config={'channel': 'channel-id'})
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         event = TransportEvent(
#             user_message_id='message-id', sent_message_id='message-id',
#             event_type='nack', nack_reason='error error')
#         yield self.outbounds.store_event(destination_id, 'message-id', event)
#         resp = yield self.get(
#             '/routers/{}/destinations/{}/messages/message-id'.format(
#                 router_id, destination_id))
#         event_dict = api_from_event(destination_id, event)
#         event_dict['timestamp'] = str(event_dict['timestamp'])
#         yield self.assert_response(
#             resp, http.OK, 'message status', {
#                 'id': 'message-id',
#                 'last_event_type': 'rejected',
#                 'last_event_timestamp': str(event['timestamp']),
#                 'events': [event_dict],
#             })

#     @inlineCallbacks
#     def test_get_destination_message_status_multiple_events(self):
#         '''Returns the last event details for last event fields, and list with
#         all events for `events`'''
#         router_config = self.create_router_config()
#         resp = yield self.post('/routers/', router_config)
#         router_id = (yield resp.json())['result']['id']

#         dest_config = self.create_destination_config(
#             config={'channel': 'channel-id'})
#         resp = yield self.post(
#             '/routers/{}/destinations/'.format(router_id), dest_config)
#         destination_id = (yield resp.json())['result']['id']

#         events = []
#         event_dicts = []
#         for i in range(5):
#             event = TransportEvent(
#                 user_message_id='message-id', sent_message_id='message-id',
#                 event_type='nack', nack_reason='error error')
#             yield self.outbounds.store_event(
#                 destination_id, 'message-id', event)
#             events.append(event)
#             event_dict = api_from_event(destination_id, event)
#             event_dict['timestamp'] = str(event_dict['timestamp'])
#             event_dicts.append(event_dict)

#         resp = yield self.get(
#             '/routers/{}/destinations/{}/messages/message-id'.format(
#                 router_id, destination_id))
#         yield self.assert_response(
#             resp, http.OK, 'message status', {
#                 'id': 'message-id',
#                 'last_event_type': 'rejected',
#                 'last_event_timestamp': event_dicts[-1]['timestamp'],
#                 'events': event_dicts,
#             })


## Test cases from the original junebug worker codebase. The ones that are left
## are for things we may not want/need anymore.

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
