import json
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from uuid import UUID

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
from vumi2.applications.errors import TimeoutError
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
        "default_from_addr": default_from_addr,
        "auth_token": None,
        "vumi_base_url_path": "",
        "turn_base_url_path": f"{http_server.bind}/message",
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


async def test_inbound_message_amqp(tca_worker, tca_ro, http_server):
    """
    Inbound messages are forwarded to the configured URL.

    This test sends the inbound message over AMQP rather than calling
    the handler directly.
    """
    msg = mkmsg("hello")
    with fail_after(2):
        print("HERE")
        await tca_ro.publish_inbound(msg)
        print("HERE2")
        req = await http_server.receive_req()
        print("HERE3")
        await http_server.send_rsp(RspInfo())
        print("HERE4")

    assert req.path == "message"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["message"]["text"]["body"] == "hello"
    assert req.body_json["contact"]["id"] == "123"


async def test_inbound_message(worker_factory, http_server):
    """
    Inbound messages are forwarded to the configured URL.

    This test calls the handler directly so we know when it's finished.
    """
    msg = mkmsg("hello")
    config = mk_config(http_server, default_from_addr=None)

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        with fail_after(2):
            async with handle_inbound(tca_worker, msg):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.body_json["message"]["text"]["body"] == "hello"
    assert req.body_json["contact"]["id"] == "123"


async def test_inbound_bad_response(worker_factory, http_server, caplog):
    """
    If an inbound message results in an HTTP error, the error and
    message are logged.
    """
    msg = mkmsg("hello")
    config = mk_config(http_server, default_from_addr=None)

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        with fail_after(2):
            async with handle_inbound(tca_worker, msg):
                await http_server.receive_req()
                await http_server.send_rsp(RspInfo(code=500))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Error sending message, received HTTP code 500" in err.getMessage()


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
            with pytest.raises(TimeoutError) as e:  # noqa: PT012
                async with handle_inbound(tca_worker, msg):
                    await http_server.receive_req()
                    await http_server.send_rsp(RspInfo(code=502, wait=0.3))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Timed out sending message after 0.2 seconds." in err.getMessage()
    assert e.value.name == "TimeoutError"
    assert e.value.description == "timeout"
    assert e.value.status == HTTPStatus.BAD_REQUEST


async def test_inbound_auth_token(worker_factory, http_server):
    """
    If mo_message_url_auth_token is set, we use its token in the
    Authorization header.
    """
    token = "my-token"  # noqa: S105 (This is a fake token.)
    config = mk_config(http_server, auth_token=token)

    msg = mkmsg("hello")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            async with handle_inbound(tca_worker, msg):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.headers["Authorization"] == "Bearer my-token"


async def test_forward_ack_bad_response(tca_worker, http_server, caplog):
    """
    If forwarding an ack results in an HTTP error, the error and event
    are logged.
    """
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
        ev = mkev("msg-21", EventType.ACK)
        with fail_after(5):
            async with handle_event(tca_worker, ev):
                await http_server.receive_req()
                await http_server.send_rsp(RspInfo(code=502, wait=0.4))

    [err] = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Timed out sending event after 0.2 seconds." in err.getMessage()


async def test_forward_nack(worker_factory, http_server):
    """
    A nack event referencing an outbound message we know about is
    forwarded over HTTP.
    """
    ev = mkev("msg-21", EventType.NACK, nack_reason="KaBooM!")

    config = mk_config(http_server, turn_base_url_path=f"{http_server.bind}/event")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        with fail_after(2):
            async with handle_event(tca_worker, ev):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["status"] == "sent"
    assert req.body_json["id"] == "msg-21"


async def test_forward_dr(worker_factory, http_server):
    """
    A delivery report event referencing an outbound message we know
    about is forwarded over HTTP.
    """
    ev = mkev("m-21", EventType.DELIVERY_REPORT, delivery_status=DeliveryStatus.PENDING)

    config = mk_config(http_server, turn_base_url_path=f"{http_server.bind}/event")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        with fail_after(2):
            async with handle_event(tca_worker, ev):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.path == "event"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["status"] == "sent"
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
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            response = await post_outbound(tca_worker, body)
            outbound = await tca_ro.consume_outbound()

    lresponse = json.loads(response)
    message_id = lresponse["messages"][0]["id"]

    assert isinstance(message_id, str)
    uuid = UUID(message_id)
    assert uuid.hex == message_id
    assert uuid.version == 4

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "None"
    assert outbound.group == "foo"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"


async def test_send_outbound_invalid_json(tca_worker, caplog):
    """
    An attempted send with a non-json body returns an appropriate error.
    """
    with fail_after(2):
        client = tca_worker.http.app.test_client()
        async with client.request(path="/messages", method="POST") as connection:
            await connection.send(b"gimme r00t?")
            await connection.send_complete()
            response = await connection.receive()

    err = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert "Error sending message, got error JsonDecodeError. Message: Expecting value: line 1 column 1 (char 0)" in err[0].getMessage()


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

    lresponse = json.loads(response)
    message_id = lresponse["messages"][0]["id"]

    assert isinstance(message_id, str)
    uuid = UUID(message_id)
    assert uuid.hex == message_id
    assert uuid.version == 4

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "None"
    assert outbound.group == "my-group"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"
