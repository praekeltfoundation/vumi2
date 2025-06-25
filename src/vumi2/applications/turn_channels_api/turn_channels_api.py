import base64
import hmac
import json
import secrets
from hashlib import sha256
from logging import getLogger
from typing import Any

import trio
from attrs import define, field
from httpx import AsyncClient
from prometheus_client import Counter
from quart import request
from trio import move_on_after

import vumi2.message_caches as message_caches
from vumi2.cli import class_from_string
from vumi2.messages import (
    Event,
    Message,
    TransportType,
)
from vumi2.workers import BaseConfig, BaseWorker

from ..errors import (
    ApiError,
    HttpErrorResponse,
    JsonDecodeError,
    SignatureMismatchError,
    TimeoutError,
)
from .messages import (
    TurnOutboundMessage,
    turn_event_from_ev,
    turn_inbound_from_msg,
    turn_outbound_from_msg,
)

turn_rate_limit_retry = Counter(
    "turn_rate_limit_retry", "Total amount of retries against the Turn API", ["attempt"]
)

LOG_MSG_HTTP_ERR = (
    "Error sending message, received HTTP code %(code)s"
    " with body %(body)s. Message: %(message)s"
)
LOG_MSG_HTTP_TIMEOUT = (
    "Timed out sending message after %(timeout)s seconds. Message: %(message)s"
)
LOG_EV_HTTP_ERR = (
    "Error sending event, received HTTP code %(code)s"
    " with body %(body)s. Event: %(event)s"
)
LOG_EV_HTTP_TIMEOUT = (
    "Timed out sending event after %(timeout)s seconds. Event: %(event)s"
)
LOG_API_ERR = (
    "Error sending message, received HTTP code %(code)s with"
    " error %(error)s. Message: %(message)s"
)

logger = getLogger(__name__)


@define(kw_only=True)
class TurnChannelsApiConfig(BaseConfig):
    # AMQP connector name. This is also used as `transport_name` for non-reply
    # outbound messages and `channel_id` for events.
    connector_name: str

    # Base URL path for HTTP requests.
    vumi_api_path: str = ""

    # Base URL path for requests to Turn.
    turn_api_url: str

    # Auth token for requests to Turn.
    auth_token: str

    # This is a required `Message` field, so we need to set it for non-reply
    # outbound messages.
    transport_type: TransportType = TransportType.SMS

    # All outbound messages must be replies or have a from address.
    default_from_addr: str

    # Maximum time allowed (in seconds) for outbound message request handling.
    request_timeout: float = 4 * 60

    # Maximum time allowed (in seconds) for inbound message and event HTTP
    # requests.
    mo_message_url_timeout: float = 10
    event_url_timeout: float = 10

    # Secret key used to sign outbound messages.
    turn_hmac_secret: str

    # Message cache class to use for storing inbound messages.
    message_cache_class: str = f"{message_caches.__name__}.MemoryMessageCache"
    message_cache_config: dict = field(factory=dict)

    # Retries
    max_retries: int = 3
    retry_delay_exponent: int = 2
    retry_delay_base: int = 2

    def vumi_url(self, path: str) -> str:
        return "/".join([self.vumi_api_path.rstrip("/"), path.lstrip("/")])

    def turn_url(self, path: str) -> str:
        return "/".join([self.turn_api_url.rstrip("/"), path.lstrip("/")])


class TurnChannelsApi(BaseWorker):
    """
    An implementation of the Turn Channels API.

    Inbound messages and events are sent over HTTP to the configured
    URL(s). Outbound messages are received over HTTP and sent to the
    configured transport.
    """

    config: TurnChannelsApiConfig

    async def setup(self) -> None:
        message_cache_class = class_from_string(self.config.message_cache_class)
        self.message_cache = message_cache_class(self.config.message_cache_config)
        await super().setup()
        self.connector = await self.setup_receive_inbound_connector(
            self.config.connector_name,
            self.handle_inbound_message,
            self.handle_event,
        )
        try:
            self.http.app.add_url_rule(
                self.config.vumi_url("/messages"),
                view_func=self.http_send_message,
                methods=["POST"],
            )
        except Exception as e:
            logger.exception(e)
            raise e
        await self.start_consuming()

    async def handle_inbound_message(self, message: Message) -> None:
        """
        Send the vumi message as an HTTP request to the configured URL.
        """

        async def _backoff(attempt: int) -> None:
            delay = self.config.retry_delay_base ** (
                attempt * self.config.retry_delay_exponent
            )
            delay = secrets.randbelow(delay)
            await trio.sleep(delay)

        logger.debug("Consuming inbound message %s", message)
        msg = turn_inbound_from_msg(message, message.transport_name)
        await self.message_cache.store_inbound(message)

        headers = {}

        headers["Authorization"] = f"Bearer {self.config.auth_token}"

        timeout = self.config.mo_message_url_timeout
        with move_on_after(timeout) as cs:
            for attempt in range(self.config.max_retries + 1):
                try:
                    async with AsyncClient() as client:
                        resp = await client.post(
                            self.config.turn_url("/messages"),
                            json=msg,
                            headers=headers,
                        )

                    if 200 <= resp.status_code < 300:
                        # Success
                        break

                    raise HttpErrorResponse(resp.status_code)

                except Exception as e:
                    turn_rate_limit_retry.labels(attempt=attempt + 1).inc()
                    logger.warning(
                        f"Attempt {attempt + 1} failed with error: {e}",
                    )
                    if attempt < self.config.max_retries:
                        await _backoff(attempt)
                    else:
                        raise

        if cs.cancelled_caught:
            logger.error(LOG_MSG_HTTP_TIMEOUT, {"timeout": timeout, "message": msg})
            raise TimeoutError()

    async def handle_event(self, event: Event) -> None:
        """
        Send the vumi event as an HTTP request to the cached event URL
        for the associated outbound message.
        """
        logger.debug("Consuming event %s", event)

        ev = turn_event_from_ev(event)

        headers = {}

        headers["Authorization"] = f"Bearer {self.config.auth_token}"

        timeout = self.config.event_url_timeout
        with move_on_after(timeout) as cs:
            async with AsyncClient() as client:
                resp = await client.post(
                    self.config.turn_url("/statuses"), json=ev, headers=headers
                )

            if resp.status_code < 200 or resp.status_code >= 300:
                logger.error(
                    LOG_EV_HTTP_ERR,
                    {"code": resp.status_code, "body": resp.text, "event": ev},
                )

        if cs.cancelled_caught:
            logger.error(LOG_EV_HTTP_TIMEOUT, {"timeout": timeout, "event": ev})

    async def http_send_message(self) -> dict[Any, Any]:
        try:
            # TODO: Log requests that timed out?
            with move_on_after(self.config.request_timeout):
                try:
                    request_data = await request.get_data(as_text=True)
                    if isinstance(request_data, bytes):
                        request_data = request_data.decode()

                    await self._verify_hmac(request_data)

                    msg_dict = json.loads(request_data)
                except json.JSONDecodeError as e:
                    raise JsonDecodeError(str(e)) from e

                logger.debug("Received outbound message: %s", msg_dict)
                if msg_dict.get("reply_to", "") == "":
                    inbound = (
                        await self.message_cache.fetch_last_inbound_by_from_address(
                            msg_dict["to"]
                        )
                    )
                    if inbound is not None:
                        msg_dict["reply_to"] = inbound.message_id

                tom = TurnOutboundMessage.deserialise(
                    msg_dict, default_from=self.config.default_from_addr
                )
                msg = await self.build_outbound(tom, inbound)

                await self.connector.publish_outbound(msg)

                rmsg = turn_outbound_from_msg(msg)
                return rmsg
        except ApiError as e:
            logger.error(
                LOG_API_ERR,
                {"code": e.status, "error": e.name, "message": e.description},
            )
            raise e

    async def _verify_hmac(self, request_data: str) -> None:
        if self.config.turn_hmac_secret:
            logger.info("Verifying HMAC signature")
            h = hmac.new(
                self.config.turn_hmac_secret.encode(),
                request_data.encode(),
                sha256,
            ).digest()
            computed_signature = base64.b64encode(h).decode("utf-8")
            signature = request.headers.get("X-Turn-Hook-Signature", "")
            logger.info(
                f"Signature from Turn: {signature}." f"Computed: {computed_signature}"
            )
            if not hmac.compare_digest(computed_signature, signature):
                raise SignatureMismatchError()

    async def build_outbound(
        self, tom: TurnOutboundMessage, inbound: Message | None = None
    ) -> Message:
        if inbound is not None:
            return tom.reply_to_vumi(inbound)
        return tom.to_vumi(self.config.connector_name, self.config.transport_type)
