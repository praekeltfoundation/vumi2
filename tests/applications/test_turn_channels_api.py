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
from trio import fail_after, open_memory_channel, open_nursery, sleep
from trio.abc import ReceiveChannel, SendChannel
from werkzeug.datastructures import Headers, MultiDict

from vumi2.applications import TurnChannelsApi
from vumi2.applications.state_cache import EventHttpInfo
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
    headers: Headers
    args: MultiDict[str, str]
    body_json: dict


@define
class RspInfo:
    code: int = 200
    body: str = ""
    wait: float = 0


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
        await sleep(rsp.wait)
        return rsp.body, rsp.code

    async def receive_req(self) -> ReqInfo:
        return await self._recv_req.receive()

    async def send_rsp(self, rsp: RspInfo):
        return await self._send_rsp.send(rsp)


@asynccontextmanager
async def handle_inbound(worker: TurnChannelsApi, msg: Message):
    async with open_nursery() as nursery:
        nursery.start_soon(worker.handle_inbound_message, msg)
        yield


async def store_inbound(worker: TurnChannelsApi, msg: Message):
    await worker.state_cache.store_inbound(msg)


async def fetch_inbound(worker: TurnChannelsApi, message_id: str):
    return await worker.state_cache.fetch_inbound(message_id)


async def store_ehi(worker: TurnChannelsApi, message_id, url, auth_token):
    await worker.state_cache.store_event_http_info(message_id, url, auth_token)


async def fetch_ehi(worker: TurnChannelsApi, message_id):
    return await worker.state_cache.fetch_event_http_info(message_id)


@asynccontextmanager
async def handle_event(worker: TurnChannelsApi, ev: Event):
    async with open_nursery() as nursery:
        nursery.start_soon(worker.handle_event, ev)
        yield


async def post_outbound(
    worker: TurnChannelsApi, msg_dict: dict, path="/messages"
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


def mk_config(
    http_server: HttpServer,
    default_from_addr: str | None = "+275554202",
    **config_update,
) -> dict:
    config = {
        "connector_name": "tca-test",
        "http_bind": "localhost:0",
        "mo_message_url": f"{http_server.bind}/message",
        "default_from_addr": default_from_addr,
    }
    return {**config, **config_update}


@pytest.fixture()
async def tca_worker(worker_factory, http_server):
    config = mk_config(http_server)
    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()
        yield worker


@pytest.fixture()
async def tca_ro(connector_factory):
    return await connector_factory.setup_ro("tca-test")


def mkmsg(content: str, to_addr="123", from_addr="456") -> Message:
    return Message(
        to_addr=to_addr,
        from_addr=from_addr,
        transport_name="tca-test",
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


def mkoutbound(
    content: str, to="+1234", from_addr="+23456", reply_to="+23456", **kw
) -> dict:
    return {
        "content": content,
        "to": to,
        "reply_to": reply_to,
        "from": from_addr,
        "context": {
            "contact": {"phone": to, "groups": [{"name": kw.get("group", "foo")}]}
        },
        "turn": {"text": {"body": content}},
        **kw,
    }


def mkreply(content: str, reply_to: str, **kw) -> dict:
    return {
        "to": kw["to"],
        "content": content,
        "reply_to": reply_to,
        "context": {"contact": {"phone": kw["to"], "groups": [{"name": "foo"}]}},
        "turn": {"text": {"body": content}},
        **kw,
    }


def parse_send_message_response(resp: bytes) -> dict:
    rjson = json.loads(resp)
    [rjson["result"]["message"].pop(key) for key in ["id", "timestamp"]]
    return rjson


async def test_inbound_message_amqp(tca_worker, tca_ro, http_server):
    """
    Inbound messages are forwarded to the configured URL.

    This test sends the inbound message over AMQP rather than calling
    the handler directly.
    """
    msg = mkmsg("hello")

    with fail_after(2):
        await tca_ro.publish_inbound(msg)
        req = await http_server.receive_req()
        await http_server.send_rsp(RspInfo())

    assert req.path == "message"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["message"]["text"]["body"] == "hello"
    assert req.body_json["contact"]["id"] == "123"

    assert await fetch_inbound(tca_worker, msg.message_id) == msg


async def test_inbound_message(tca_worker, http_server):
    """
    Inbound messages are forwarded to the configured URL.

    This test calls the handler directly so we know when it's finished.
    """
    msg = mkmsg("hello")

    with fail_after(2):
        async with handle_inbound(tca_worker, msg):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.body_json["message"]["text"]["body"] == "hello"
    assert req.body_json["contact"]["id"] == "123"

    assert await fetch_inbound(tca_worker, msg.message_id) == msg


async def test_inbound_bad_response(tca_worker, http_server, caplog):
    """
    If an inbound message results in an HTTP error, the error and
    message are logged.
    """
    msg = mkmsg("hello")

    with fail_after(2):
        async with handle_inbound(tca_worker, msg):
            await http_server.receive_req()
            await http_server.send_rsp(RspInfo(code=500))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Error sending message, received HTTP code 500" in err.getMessage()

    assert await fetch_inbound(tca_worker, msg.message_id) == msg


async def test_inbound_too_slow(worker_factory, http_server, caplog):
    """
    If an inbound message times out, the error and message are logged.

    Using an autojump clock here seems to break the AMQP client, so we
    have to use wall-clock time instead.
    """
    config = mk_config(http_server, mo_message_url_timeout=0.2)
    msg = mkmsg("hello")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(5):
            async with handle_inbound(tca_worker, msg):
                await http_server.receive_req()
                await http_server.send_rsp(RspInfo(code=502, wait=0.3))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Timed out sending message after 0.2 seconds." in err.getMessage()

    assert await fetch_inbound(tca_worker, msg.message_id) == msg


async def test_inbound_basic_auth_url(worker_factory, http_server):
    """
    If mo_message_url has credentials in it, those get sent as an
    Authorization header.
    """
    url = f"{http_server.bind}/message".replace("http://", "http://foo:bar@")
    config = mk_config(http_server, mo_message_url=url)

    msg = mkmsg("hello")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            async with handle_inbound(tca_worker, msg):
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

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            async with handle_inbound(tca_worker, msg):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.headers["Authorization"] == "Token my-token"


async def test_event_no_message_id(tca_worker, http_server, caplog):
    """
    If we receive an event but don't have anything stored for the
    message_id it refers to, we log it and move on.
    """
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        await tca_worker.handle_event(ev)

    [warn] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert "Cannot find event URL, missing user_message_id" in warn.getMessage()


async def test_forward_ack_amqp(tca_worker, tca_ro, http_server):
    """
    An ack event referencing an outbound message we know about is
    forwarded over HTTP.

    This test sends the inbound message over AMQP rather than calling
    the handler directly.
    """
    await store_ehi(tca_worker, "msg-21", f"{http_server.bind}/event", None)
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        await tca_ro.publish_event(ev)
        req = await http_server.receive_req()
        await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["event_type"] == "sent"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "tca-test"
    assert req.body_json["id"] == "msg-21"


async def test_forward_ack(tca_worker, http_server):
    """
    An ack event referencing an outbound message we know about is
    forwarded over HTTP.
    """
    await store_ehi(tca_worker, "msg-21", f"{http_server.bind}/event", None)
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        async with handle_event(tca_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["event_type"] == "sent"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "tca-test"
    assert req.body_json["id"] == "msg-21"


async def test_forward_ack_basic_auth_url(tca_worker, http_server):
    """
    If an event's URL has credentials in it, we use them for basic auth
    when forwarding over HTTP.
    """
    url = f"{http_server.bind}/event".replace("http://", "http://foo:bar@")
    await store_ehi(tca_worker, "msg-21", url, None)
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        async with handle_event(tca_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    basic = b64encode(b"foo:bar").decode()  # base64 is all bytes, not strs.
    assert req.headers["Authorization"] == f"Basic {basic}"
    assert req.body_json["event_type"] == "sent"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "tca-test"
    assert req.body_json["id"] == "msg-21"


async def test_forward_ack_auth_token(tca_worker, http_server):
    """
    If an event has an auth token associated with it, we use that when
    forwarding over HTTP.
    """
    url = f"{http_server.bind}/event"
    await store_ehi(tca_worker, "msg-21", url, "my-event-token")
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        async with handle_event(tca_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["Authorization"] == "Token my-event-token"
    assert req.body_json["event_type"] == "sent"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "tca-test"
    assert req.body_json["id"] == "msg-21"


async def test_event_default_url(worker_factory, http_server, caplog):
    """
    If we have a default event url configured, events with stored
    message info are sent the their stored url and events without are
    sent to the default url.
    """
    config = mk_config(http_server, default_event_url=f"{http_server.bind}/allevents")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        await store_ehi(tca_worker, "msg-21", f"{http_server.bind}/event21", None)
        ev21 = mkev("msg-21", EventType.ACK)
        ev22 = mkev("msg-22", EventType.ACK)
        with fail_after(2):
            async with handle_event(tca_worker, ev21):
                req21 = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())
            async with handle_event(tca_worker, ev22):
                req22 = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req21.path == "event21"
    assert req21.headers["Content-Type"] == "application/json"
    assert req21.body_json["event_type"] == "sent"
    assert req21.body_json["event_details"] == {}
    assert req21.body_json["channel_id"] == "tca-test"

    assert req22.path == "allevents"
    assert req22.headers["Content-Type"] == "application/json"
    assert req22.body_json["event_type"] == "sent"
    assert req22.body_json["event_details"] == {}
    assert req22.body_json["channel_id"] == "tca-test"


async def test_event_default_url_and_auth(worker_factory, http_server, caplog):
    """
    If we have a default event url and token configured, events with no
    stored message info are sent to the default url with token auth.
    """
    config = mk_config(
        http_server,
        default_event_url=f"{http_server.bind}/allevents",
        default_event_auth_token="tok",  # noqa: S106 (This is a fake token.)
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        ev = mkev("msg-21", EventType.ACK)
        with fail_after(2):
            async with handle_event(tca_worker, ev):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.path == "allevents"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["Authorization"] == "Token tok"
    assert req.body_json["event_type"] == "sent"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "tca-test"


async def test_forward_ack_bad_response(tca_worker, http_server, caplog):
    """
    If forwarding an ack results in an HTTP error, the error and event
    are logged.
    """
    await store_ehi(tca_worker, "msg-21", f"{http_server.bind}/event", None)
    ev = mkev("msg-21", EventType.ACK)

    with fail_after(2):
        async with handle_event(tca_worker, ev):
            await http_server.receive_req()
            await http_server.send_rsp(RspInfo(code=500))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Error sending event, received HTTP code 500" in err.getMessage()


async def test_forward_ack_too_slow(worker_factory, http_server, caplog):
    """
    If forwarding an ack times out, the error and message are logged.

    Using an autojump clock here seems to break the AMQP client, so we
    have to use wall-clock time instead.
    """
    config = mk_config(http_server, event_url_timeout=0.2)

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        await store_ehi(tca_worker, "msg-21", f"{http_server.bind}/event", None)
        ev = mkev("msg-21", EventType.ACK)
        with fail_after(5):
            async with handle_event(tca_worker, ev):
                await http_server.receive_req()
                await http_server.send_rsp(RspInfo(code=502, wait=0.4))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Timed out sending event after 0.2 seconds." in err.getMessage()


async def test_forward_nack(tca_worker, http_server):
    """
    A nack event referencing an outbound message we know about is
    forwarded over HTTP.
    """
    await store_ehi(tca_worker, "msg-21", f"{http_server.bind}/event", None)
    ev = mkev("msg-21", EventType.NACK, nack_reason="KaBooM!")

    with fail_after(2):
        async with handle_event(tca_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["event_type"] == "sent"
    assert req.body_json["event_details"] == {"reason": "KaBooM!"}
    assert req.body_json["channel_id"] == "tca-test"
    assert req.body_json["id"] == "msg-21"


async def test_forward_dr(tca_worker, http_server):
    """
    A delivery report event referencing an outbound message we know
    about is forwarded over HTTP.
    """
    await store_ehi(tca_worker, "m-21", f"{http_server.bind}/event", None)
    ev = mkev("m-21", EventType.DELIVERY_REPORT, delivery_status=DeliveryStatus.PENDING)

    with fail_after(2):
        async with handle_event(tca_worker, ev):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["event_type"] == "sent"
    assert req.body_json["event_details"] == {}
    assert req.body_json["channel_id"] == "tca-test"
    assert req.body_json["id"] == "m-21"


async def test_send_outbound(worker_factory, http_server, tca_ro):
    """
    An outbound message received over HTTP is forwarded over AMQP.

    We don't store anything if event_url is unset.
    """
    body = mkoutbound("foo")

    config = mk_config(
        http_server,
        None,
        default_event_url=f"{http_server.bind}/event",
        default_event_auth_token=None,
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            response = await post_outbound(tca_worker, body)
            outbound = await tca_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "contact": {
                "id": "+1234",
                "profile": {
                    "name": "+1234",
                },
            },
            "message": {
                "from": "None",
                "text": {
                    "body": "foo",
                },
                "type": "text",
            },
        },
    }

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "None"
    assert outbound.group == "foo"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"

    assert await fetch_ehi(tca_worker, outbound.message_id) is None


async def test_send_outbound_event_url(worker_factory, tca_ro, http_server):
    """
    An outbound message with event_url set stores event info.
    """
    body = mkoutbound("foo", event_url=f"{http_server.bind}/event", reply_to=None)

    config = mk_config(
        http_server,
        None,
        default_event_url=f"{http_server.bind}/event",
        default_event_auth_token=None,
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            response = await post_outbound(tca_worker, body)
            outbound = await tca_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "contact": {
                "id": "+1234",
                "profile": {
                    "name": "+1234",
                },
            },
            "message": {
                "from": "None",
                "text": {
                    "body": "foo",
                },
                "type": "text",
            },
        },
    }

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "None"
    assert outbound.group == "foo"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"

    ehi = await fetch_ehi(tca_worker, outbound.message_id)
    assert ehi == EventHttpInfo(f"{http_server.bind}/event", None)


async def test_send_outbound_event_auth(worker_factory, tca_ro, http_server):
    """
    An outbound message with event_url and event_auth_token set stores event info.
    """
    token = "token"  # noqa: S105 (This is a fake token.)
    body = mkoutbound(
        "foo", event_url=f"{http_server.bind}/event", event_auth_token=token
    )
    config = mk_config(
        http_server,
        None,
        default_event_url=f"{http_server.bind}/event",
        default_event_auth_token=token,
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            response = await post_outbound(tca_worker, body)
            outbound = await tca_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "contact": {
                "id": "+1234",
                "profile": {
                    "name": "+1234",
                },
            },
            "message": {
                "from": "None",
                "text": {
                    "body": "foo",
                },
                "type": "text",
            },
        },
    }

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "None"
    assert outbound.group == "foo"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"

    ehi = await fetch_ehi(tca_worker, outbound.message_id)
    assert ehi == EventHttpInfo(f"{http_server.bind}/event", "token")


async def test_send_outbound_invalid_json(tca_worker):
    """
    An attempted send with a non-json body returns an appropriate error.
    """
    with fail_after(2):
        client = tca_worker.http.app.test_client()
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


async def test_send_outbound_group(worker_factory, http_server, tca_ro):
    """
    An outbound group message received over HTTP is forwarded over AMQP.
    """
    body = mkoutbound("foo", group="my-group")
    config = mk_config(
        http_server,
        None,
        default_event_url=f"{http_server.bind}/event",
        default_event_auth_token=None,
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            response = await post_outbound(tca_worker, body)
            outbound = await tca_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "contact": {
                "id": "+1234",
                "profile": {
                    "name": "+1234",
                },
            },
            "message": {
                "from": "None",
                "text": {
                    "body": "foo",
                },
                "type": "text",
            },
        },
    }

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "None"
    assert outbound.group == "my-group"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"


async def test_send_outbound_reply(worker_factory, http_server, tca_ro):
    """
    An outbound reply message received over HTTP is forwarded over AMQP
    if the original message can be found.
    """
    inbound = mkmsg("inbound")
    body = mkreply("late", reply_to=inbound.message_id, to="+6789", **{"from": "+9876"})

    config = mk_config(
        http_server,
        inbound.message_id,
        default_event_url=f"{http_server.bind}/event",
        default_event_auth_token=None,
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        await store_inbound(tca_worker, inbound)
        with fail_after(2):
            response = await post_outbound(tca_worker, body)
            outbound = await tca_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "contact": {"id": "456", "profile": {"name": "456"}},
            "message": {
                "type": "text",
                "text": {
                    "body": "late",
                },
                "from": "123",
            },
        },
    }

    assert outbound.to_addr == "456"
    assert outbound.from_addr == "123"
    assert outbound.group is None
    assert outbound.in_reply_to == inbound.message_id
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "late"


async def test_send_outbound_reply_no_stored_inbound(tca_worker):
    """
    An outbound reply message received over HTTP cannot be forwarded
    over AMQP if the original message cannot be found.
    """
    body = mkreply("foo", reply_to="no-such-message", to="")
    with fail_after(2):
        response = await post_outbound(tca_worker, body)

    expected_err = {
        "message": "Inbound message with id +275554202 not found",
        "type": "MessageNotFound",
    }
    assert json.loads(response) == {
        "status": 400,
        "code": "Bad Request",
        "description": "message not found",
        "result": {"errors": [expected_err]},
    }


async def test_send_outbound_no_to_or_reply(tca_worker, tca_ro):
    """
    An outbound reply message received over HTTP is forwarded over AMQP
    if the original message can be found.
    """
    with fail_after(2):
        response = await post_outbound(tca_worker, {"content": "going nowhere"})

    expected_err = {
        "message": "Missing key: context",
        "type": "ApiUsageError",
    }
    assert json.loads(response) == {
        "status": 400,
        "code": "Bad Request",
        "description": "api usage error",
        "result": {"errors": [expected_err]},
    }


async def test_send_outbound_expired_allowed(worker_factory, http_server, tca_ro):
    """
    If an outbound reply has a to address and we're allowed to send
    expired replies, we send it as a non-reply.
    """
    body = mkreply("late", reply_to="expired", to="+6789", **{"from": "+9876"})

    config = mk_config(http_server, allow_expired_replies=True)

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            response = await post_outbound(tca_worker, body)
            outbound = await tca_ro.consume_outbound()

    assert parse_send_message_response(response) == {
        "status": 201,
        "code": "Created",
        "description": "message submitted",
        "result": {
            "contact": {"id": "+6789", "profile": {"name": "+6789"}},
            "message": {
                "type": "text",
                "text": {
                    "body": "late",
                },
                "from": "+275554202",
            },
        },
    }

    assert outbound.to_addr == "+6789"
    assert outbound.from_addr == "+275554202"
    assert outbound.group == "foo"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "late"
