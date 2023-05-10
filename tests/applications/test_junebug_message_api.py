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


async def test_inbound_bad_response(jma_worker, http_server, caplog):
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


async def test_inbound_basic_auth_url(worker_factory, http_server):
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


async def test_inbound_auth_token(worker_factory, http_server):
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


async def test_send_outbound_expired_allowed(worker_factory, http_server, jma_ro):
    """
    If an outbound reply has a to address and we're allowed to send
    expired replies, we send it as a non-reply.
    """
    body = mkreply("late", reply_to="expired", to="+6789", **{"from": "+9876"})

    config = mk_config(http_server, allow_expired_replies=True)

    async with worker_factory.with_cleanup(JunebugMessageApi, config) as jma_worker:
        await jma_worker.setup()
        with fail_after(2):
            response = await post_outbound(jma_worker, body)
            outbound = await jma_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "to": "+6789",
            "from": "+9876",
            "group": None,
            "reply_to": None,
            "channel_id": "jma-test",
            "channel_data": {},
            "content": "late",
        },
    }

    assert outbound.to_addr == "+6789"
    assert outbound.from_addr == "+9876"
    assert outbound.group is None
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "jma-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "late"
