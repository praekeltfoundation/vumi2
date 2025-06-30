import base64
import hmac
import json
import logging
from contextlib import asynccontextmanager
from hashlib import sha256
from http import HTTPStatus
from unittest.mock import patch
from uuid import UUID

import httpx
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
from vumi2.applications.errors import HttpErrorResponse, JsonDecodeError, TimeoutError
from vumi2.messages import (
    DeliveryStatus,
    Event,
    EventType,
    Message,
    Session,
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
    worker: TurnChannelsApi, msg_dict: dict, path="/messages", signature=""
) -> bytes:
    client = worker.http.app.test_client()
    headers = {"Content-Type": "application/json", "X-Turn-Hook-Signature": signature}
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
        "vumi_api_path": "",
        "turn_api_url": f"{http_server.bind}",
        "turn_hmac_secret": "supersecret",
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
    content: str,
    to="+1234",
    waiting_for_user_input=False,
    **kw,
) -> dict:
    return {
        "block": None,
        "resources": None,
        "evaluated_resources": None,
        "content": content,
        "to": to,
        # Turn doesn't send these fields
        # "reply_to": reply_to,
        # "from": from_addr,
        # Context from Turn appears to be null,
        # but we were expecting this to be {"contact": {"phone": to}},
        "context": None,
        "turn": {"type": "text", "text": {"body": content}},
        "waiting_for_user_input": waiting_for_user_input,
        **kw,
    }


def mkreply(content: str, reply_to: str, **kw) -> dict:
    return {
        "to": kw["to"],
        "content": content,
        "reply_to": reply_to,
        "context": {"contact": {"phone": kw["to"]}},
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
        await tca_ro.publish_inbound(msg)
        req = await http_server.receive_req()
        await http_server.send_rsp(RspInfo())

    assert req.path == "messages"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["message"]["text"]["body"] == "hello"
    assert req.body_json["contact"]["id"] == "456"


async def test_inbound_message(worker_factory, http_server):
    """
    Inbound messages are forwarded to the configured URL.

    This test calls the handler directly so we know when it's finished.
    """
    msg = mkmsg("hello")
    config = mk_config(http_server, default_from_addr=None)

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            async with handle_inbound(tca_worker, msg):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())
    inbound = await tca_worker.message_cache.fetch_last_inbound_by_from_address("456")
    assert inbound == msg
    assert req.body_json["message"]["text"]["body"] == "hello"
    assert req.body_json["contact"]["id"] == "456"
    assert req.body_json["message"]["from"] == "456"


@pytest.mark.asyncio()
async def test_inbound_bad_response(worker_factory, http_server, caplog):
    """
    If an inbound message results in an HTTP error, the error and
    message are logged.
    """
    # Configure with shorter timeouts and retries for testing
    config = mk_config(
        http_server,
        default_from_addr=None,
        mo_message_url_timeout=2.0,  # Shorter timeout for test
        max_retries=1,  # Only retry once
        retry_delay_base=0.1,  # Shorter delay for test
    )
    msg = mkmsg("hello")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()

        with pytest.raises(HttpErrorResponse):  # noqa: PT012
            async with handle_inbound(worker, msg):
                req = await http_server.receive_req()
                assert req.body_json["message"]["text"]["body"] == "hello"
                await http_server.send_rsp(RspInfo(code=500))

                req = await http_server.receive_req()
                assert req.body_json["message"]["text"]["body"] == "hello"
                await http_server.send_rsp(RspInfo(code=500))

        warning_logs = [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        ]

        assert any(
            "Attempt 1 failed with error: HTTP error response: 500" in msg
            for msg in warning_logs
        )


@pytest.mark.asyncio()
async def test_inbound_too_slow(worker_factory, http_server, caplog):
    """
    If an inbound message times out, the error and message are logged.
    """
    # Set a very short timeout and disable retries for this test
    config = mk_config(
        http_server,
        mo_message_url_timeout=0.1,
        max_retries=0,  # Disable retries for this test
    )
    msg = mkmsg("hello")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()

        with pytest.raises(TimeoutError) as exc_info:
            async with handle_inbound(tca_worker, msg):
                # Don't respond to the request to trigger a timeout
                pass

    error_logs = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR
    ]

    assert any(
        "Timed out sending message after 0.1 seconds. Message:" in msg
        for msg in error_logs
    )

    assert exc_info.value.name == "TimeoutError"
    assert exc_info.value.description == "timeout"
    assert exc_info.value.status == HTTPStatus.BAD_REQUEST


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

    config = mk_config(http_server)

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        with fail_after(2):
            async with handle_event(tca_worker, ev):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.path == "statuses"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["status"]["status"] == "sent"
    assert req.body_json["status"]["id"] == "msg-21"


async def test_forward_dr(worker_factory, http_server):
    """
    A delivery report event referencing an outbound message we know
    about is forwarded over HTTP.
    """
    ev = mkev("m-21", EventType.DELIVERY_REPORT, delivery_status=DeliveryStatus.PENDING)

    config = mk_config(http_server)

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        with fail_after(2):
            async with handle_event(tca_worker, ev):
                req = await http_server.receive_req()
                await http_server.send_rsp(RspInfo())

    assert req.path == "statuses"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body_json["status"]["status"] == "sent"
    assert req.body_json["status"]["id"] == "m-21"


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
            h = hmac.new(
                config["turn_hmac_secret"].encode(), json.dumps(body).encode(), sha256
            ).digest()
            computed_signature = base64.b64encode(h).decode("utf-8")
            response = await post_outbound(
                tca_worker, body, signature=computed_signature
            )
            outbound = await tca_ro.consume_outbound()

    lresponse = json.loads(response)
    message_id = lresponse["messages"][0]["id"]

    assert isinstance(message_id, str)
    uuid = UUID(message_id)
    assert uuid.hex == message_id
    assert uuid.version == 4

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "None"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.content == "foo"
    assert outbound.helper_metadata == {}
    assert outbound.session_event == Session.CLOSE


async def test_send_outbound_reply(worker_factory, http_server, tca_ro):
    """
    An outbound message received over HTTP is forwarded over AMQP.

    We don't store anything if event_url is unset.
    """
    inbound = mkmsg("hello", from_addr="+1234")
    body = mkoutbound("foo")

    config = mk_config(
        http_server,
        None,
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        await tca_worker.message_cache.store_inbound(inbound)
        with fail_after(2):
            h = hmac.new(
                config["turn_hmac_secret"].encode(), json.dumps(body).encode(), sha256
            ).digest()
            computed_signature = base64.b64encode(h).decode("utf-8")
            response = await post_outbound(
                tca_worker, body, signature=computed_signature
            )
            outbound = await tca_ro.consume_outbound()

    lresponse = json.loads(response)
    message_id = lresponse["messages"][0]["id"]

    assert isinstance(message_id, str)
    uuid = UUID(message_id)
    assert uuid.hex == message_id
    assert uuid.version == 4

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "123"
    assert outbound.in_reply_to == inbound.message_id
    assert outbound.transport_name == "tca-test"
    assert outbound.content == "foo"
    assert outbound.helper_metadata == {}
    assert outbound.session_event == Session.CLOSE


async def test_send_outbound_invalid_hmac(tca_worker, caplog):
    """
    An outbound message with an invalid hmac generates an error
    """
    body = mkoutbound("foo")
    message = json.dumps(body)

    with fail_after(2):
        client = tca_worker.http.app.test_client()
        h = hmac.new(b"fake news", message.encode(), sha256).digest()
        computed_signature = base64.b64encode(h).decode("utf-8")
        async with client.request(
            path="/messages",
            method="POST",
            headers={"X-Turn-Hook-Signature": computed_signature},
        ) as connection:
            await connection.send(message.encode())
            await connection.send_complete()
            await connection.receive()

    err = [log for log in caplog.records if log.levelno >= logging.ERROR]
    assert (
        "Error sending message, received HTTP code 401 with error "
        "SignatureMismatchError. Message: Authentication failed: Invalid HMAC signature"
        in err[0].getMessage()
    )


async def test_send_outbound_invalid_json(worker_factory, http_server, caplog):
    """
    An attempted send with a non-json body returns an appropriate error.
    """
    config = mk_config(http_server)
    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()
        message = "gimme r00t?"
        h = hmac.new(
            worker.config.turn_hmac_secret.encode(), message.encode(), sha256
        ).digest()
        computed_signature = base64.b64encode(h).decode("utf-8")

        async with worker.http.app.test_request_context(
            "/messages",
            method="POST",
            data=message,
            headers={
                "Content-Type": "application/json",
                "X-Turn-Hook-Signature": computed_signature,
            },
        ) as ctx:
            await ctx.push()
            try:
                with pytest.raises(JsonDecodeError):
                    await worker.http_send_message()
            finally:
                await ctx.pop()

        err = [log for log in caplog.records if log.levelno >= logging.ERROR]
        error_messages = [log.getMessage() for log in err]
        assert any(
            "json decode error" in msg for msg in error_messages
        ), f"Expected 'json decode error' in error messages, but got: {error_messages}"


async def test_send_outbound_times_out(worker_factory, http_server, caplog):
    """
    When an HTTP request times out, we log an error and raise a TimeoutError.
    """
    config = mk_config(http_server, request_timeout=0.1)
    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        # Create a mock for get_data that will sleep longer
        # than the timeout, causing the request to time out in the move_on_after block
        async def mock_get_data(as_text=False):
            await sleep(0.2)
            return "{}" if as_text else b"{}"

        async with worker.http.app.test_request_context(
            "/messages",
            method="POST",
            data="{}",
            headers={"Content-Type": "application/json"},
        ) as ctx:
            await ctx.push()
            try:
                with patch("quart.request.get_data", new=mock_get_data):
                    with pytest.raises(TimeoutError):
                        await worker.http_send_message()
            finally:
                await ctx.pop()

    assert any(
        "Timed out sending message after" in str(record) for record in caplog.records
    )


async def test_verify_hmac_timeout(worker_factory, http_server, caplog):
    """
    When _verify_hmac takes too long, we should log an error and raise a TimeoutError.
    """
    body = mkoutbound("foo")
    test_message = json.dumps(body)

    config = mk_config(http_server, request_timeout=0.1)
    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()

        async def mock_verify_hmac(*args, **kwargs):
            await sleep(0.2)
            return await original_verify_hmac(*args, **kwargs)

        original_verify_hmac = worker._verify_hmac

        h = hmac.new(
            worker.config.turn_hmac_secret.encode(), test_message.encode(), sha256
        ).digest()
        computed_signature = base64.b64encode(h).decode("utf-8")

        async with worker.http.app.test_request_context(
            "/messages",
            method="POST",
            data=test_message,
            headers={
                "Content-Type": "application/json",
                "X-Turn-Hook-Signature": computed_signature,
            },
        ) as ctx:
            await ctx.push()
            try:
                with patch.object(worker, "_verify_hmac", new=mock_verify_hmac):
                    with pytest.raises(TimeoutError):
                        await worker.http_send_message()
            finally:
                await ctx.pop()

    assert any(
        "Timed out sending message after" in str(record) for record in caplog.records
    )


async def test_fetch_inbound_timeout(worker_factory, http_server, caplog):
    """
    When fetch_last_inbound_by_from_address times out,
    we should log an error and raise a TimeoutError.
    """
    body = mkoutbound("foo")
    test_message = json.dumps(body)

    config = mk_config(http_server, request_timeout=0.1)
    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()

        async def fetch_last_inbound_by_from_address(*args, **kwargs):
            await sleep(0.2)
            return None

        h = hmac.new(
            worker.config.turn_hmac_secret.encode(), test_message.encode(), sha256
        ).digest()
        computed_signature = base64.b64encode(h).decode("utf-8")

        async with worker.http.app.test_request_context(
            "/messages",
            method="POST",
            data=test_message,
            headers={
                "Content-Type": "application/json",
                "X-Turn-Hook-Signature": computed_signature,
            },
        ) as ctx:
            await ctx.push()
            try:
                with patch.object(
                    worker.message_cache,
                    "fetch_last_inbound_by_from_address",
                    new=fetch_last_inbound_by_from_address,
                ):
                    with pytest.raises(TimeoutError):
                        await worker.http_send_message()
            finally:
                await ctx.pop()

    assert any(
        "Timed out sending message after" in str(record) for record in caplog.records
    )


async def test_build_outbound_timeout(worker_factory, http_server, caplog):
    """
    When build_outbound times out, we should log an error and raise a TimeoutError.
    """
    body = mkoutbound("foo")
    test_message = json.dumps(body)

    config = mk_config(http_server, request_timeout=0.1)
    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()

        async def mock_build_outbound(*args, **kwargs):
            await sleep(0.2)
            return Message("to_addr", "from_addr", "transport_name", TransportType.SMS)

        h = hmac.new(
            worker.config.turn_hmac_secret.encode(), test_message.encode(), sha256
        ).digest()
        computed_signature = base64.b64encode(h).decode("utf-8")

        async with worker.http.app.test_request_context(
            "/messages",
            method="POST",
            data=test_message,
            headers={
                "Content-Type": "application/json",
                "X-Turn-Hook-Signature": computed_signature,
            },
        ) as ctx:
            await ctx.push()
            try:
                with patch.object(worker, "build_outbound", new=mock_build_outbound):
                    with pytest.raises(TimeoutError):
                        await worker.http_send_message()
            finally:
                await ctx.pop()

    assert any(
        "Timed out sending message after" in str(record) for record in caplog.records
    )


async def test_publish_outbound_timeout(worker_factory, http_server, caplog):
    """
    When publish_outbound times out, we should log an error and raise a TimeoutError.
    """
    body = mkoutbound("foo")
    test_message = json.dumps(body)

    config = mk_config(http_server, request_timeout=0.1)
    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()

        async def mock_publish_outbound(msg):
            await sleep(0.2)
            return None

        h = hmac.new(
            worker.config.turn_hmac_secret.encode(), test_message.encode(), sha256
        ).digest()
        computed_signature = base64.b64encode(h).decode("utf-8")

        async with worker.http.app.test_request_context(
            "/messages",
            method="POST",
            data=test_message,
            headers={
                "Content-Type": "application/json",
                "X-Turn-Hook-Signature": computed_signature,
            },
        ) as ctx:
            await ctx.push()
            try:
                with patch.object(
                    worker.connector, "publish_outbound", new=mock_publish_outbound
                ):
                    with pytest.raises(TimeoutError):
                        await worker.http_send_message()
            finally:
                await ctx.pop()

    assert any(
        "Timed out sending message after" in str(record) for record in caplog.records
    )


@pytest.mark.asyncio()
async def test_retry_on_http_error(worker_factory, http_server, caplog):
    """
    When an HTTP error occurs, we retry according to the retry configuration.
    """
    config = mk_config(
        http_server,
        max_retries=2,
        retry_delay_base=1,
        retry_delay_exponent=1,
    )
    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()
        msg = mkmsg("test")

        async with handle_inbound(worker, msg):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo(code=500))

            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo(code=503))

            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo(code=200))

        assert req.body_json["message"]["text"]["body"] == "test"

        log_messages = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]

        assert any(
            "Attempt 1 failed with error: HTTP error response: 500" in msg
            for msg in log_messages
        )
        assert any(
            "Attempt 2 failed with error: HTTP error response: 503" in msg
            for msg in log_messages
        )


@pytest.mark.asyncio()
async def test_retry_on_network_error(worker_factory, http_server, caplog, monkeypatch):
    """
    When a network error occurs, we retry according to the retry configuration.
    """
    call_count = 0
    original_post = httpx.AsyncClient.post

    async def mock_post(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("Connection failed")
        return await original_post(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    config = mk_config(
        http_server,
        max_retries=1,
        retry_delay_base=0.1,
        retry_delay_exponent=1,
    )

    caplog.clear()
    caplog.set_level(logging.WARNING, logger="vumi2")

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as worker:
        await worker.setup()
        msg = mkmsg("test")

        async with handle_inbound(worker, msg):
            req = await http_server.receive_req()
            await http_server.send_rsp(RspInfo(code=200))

        assert req.body_json["message"]["text"]["body"] == "test"

        assert call_count == 2, f"Expected 2 calls, got {call_count}"

        warning_logs = [
            record.message
            for record in caplog.records
            if record.levelno == logging.WARNING
        ]

        expected_log = "Attempt 1 failed with error: Connection failed"
        assert any(
            expected_log in log for log in warning_logs
        ), f"Expected warning containing '{expected_log}', got: {warning_logs}"


async def test_send_outbound_group(worker_factory, http_server, tca_ro):
    """
    An outbound group message received over HTTP is forwarded over AMQP.
    """
    body = mkoutbound("foo", group="my-group", waiting_for_user_input=True)
    config = mk_config(
        http_server,
        None,
        default_event_url=f"{http_server.bind}/event",
        default_event_auth_token=None,
    )

    async with worker_factory.with_cleanup(TurnChannelsApi, config) as tca_worker:
        await tca_worker.setup()
        with fail_after(2):
            h = hmac.new(
                config["turn_hmac_secret"].encode(), json.dumps(body).encode(), sha256
            ).digest()
            computed_signature = base64.b64encode(h).decode("utf-8")
            response = await post_outbound(
                tca_worker, body, signature=computed_signature
            )
            outbound = await tca_ro.consume_outbound()

    lresponse = json.loads(response)
    message_id = lresponse["messages"][0]["id"]

    assert isinstance(message_id, str)
    uuid = UUID(message_id)
    assert uuid.hex == message_id
    assert uuid.version == 4

    assert outbound.to_addr == "+1234"
    assert outbound.from_addr == "None"
    assert outbound.in_reply_to is None
    assert outbound.transport_name == "tca-test"
    assert outbound.helper_metadata == {}
    assert outbound.content == "foo"
    assert outbound.session_event == Session.RESUME
