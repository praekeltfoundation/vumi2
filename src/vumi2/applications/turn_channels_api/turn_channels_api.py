import base64
import hmac
import json
from hashlib import sha256
from http import HTTPStatus
from logging import getLogger
from typing import Any

from attrs import define
from httpx import AsyncClient
from quart import request
from trio import move_on_after

from vumi2.messages import (
    Event,
    Message,
    TransportType,
    generate_message_id,
)
from vumi2.workers import BaseConfig, BaseWorker

from ..errors import ApiError, JsonDecodeError, SignatureMismatchError, TimeoutError
from .messages import (
    TurnOutboundMessage,
    turn_event_from_ev,
    turn_inbound_from_msg,
    turn_outbound_from_msg,
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
LOG_API_ERR = "Error sending message, got error %(error)s. Message: %(message)s"

logger = getLogger(__name__)


@define(kw_only=True)
class TurnChannelsApiConfig(BaseConfig):
    # AMQP connector name. This is also used as `transport_name` for non-reply
    # outbound messages and `channel_id` for events.
    connector_name: str

    # Base URL path for HTTP requests.
    vumi_base_url_path: str = ""

    # Base URL path for requests to Turn.
    turn_base_url_path: str = ""

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
    secret_key: str

    def vumi_url(self, path: str) -> str:
        return "/".join([self.vumi_base_url_path.rstrip("/"), path.lstrip("/")])


class TurnChannelsApi(BaseWorker):
    """
    An implementation of the Turn Channels API.

    Inbound messages and events are sent over HTTP to the configured
    URL(s). Outbound messages are received over HTTP and sent to the
    configured transport.
    """

    config: TurnChannelsApiConfig

    async def setup(self) -> None:
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
        logger.debug("Consuming inbound message %s", message)
        msg = turn_inbound_from_msg(message, message.transport_name)

        headers = {}

        headers["Authorization"] = f"Bearer {self.config.auth_token}"

        timeout = self.config.mo_message_url_timeout
        with move_on_after(timeout) as cs:
            async with AsyncClient() as client:
                resp = await client.post(
                    self.config.turn_base_url_path.format(message.message_id),
                    json=msg,
                    headers=headers,
                )

            # TODO: Deal with API rate limits
            if resp.status_code < 200 or resp.status_code >= 300:
                logger.error(
                    LOG_MSG_HTTP_ERR,
                    {"code": resp.status_code, "body": resp.text, "message": msg},
                )

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
        url = self.config.turn_base_url_path.format(event.user_message_id)

        headers["Authorization"] = f"Bearer {self.config.auth_token}"

        timeout = self.config.event_url_timeout
        with move_on_after(timeout) as cs:
            async with AsyncClient() as client:
                resp = await client.post(url, json=ev, headers=headers)

            if resp.status_code < 200 or resp.status_code >= 300:
                logger.error(
                    LOG_EV_HTTP_ERR,
                    {"code": resp.status_code, "body": resp.text, "event": ev},
                )

        if cs.cancelled_caught:
            logger.error(LOG_EV_HTTP_TIMEOUT, {"timeout": timeout, "event": ev})

    async def http_send_message(self) -> dict[Any, Any]:
        _message_id = generate_message_id()
        try:
            # TODO: Log requests that timed out?
            with move_on_after(self.config.request_timeout):
                try:
                    request_data = await request.get_data(as_text=True)
                    if isinstance(request_data, bytes):
                        request_data = request_data.decode()
                    # Verify the hmac signature
                    h = hmac.new(
                        self.config.secret_key.encode(), request_data.encode(), sha256
                    ).digest()
                    computed_signature = str(base64.b64encode(h))
                    signature = request.headers.get("X-Turn-Hook-Signature", "")
                    if not hmac.compare_digest(computed_signature, signature):
                        raise SignatureMismatchError()

                    msg_dict = json.loads(request_data)
                except json.JSONDecodeError as e:
                    raise JsonDecodeError(str(e)) from e

                logger.debug("Received outbound message: %s", msg_dict)

                tom = TurnOutboundMessage.deserialise(
                    msg_dict, default_from=self.config.default_from_addr
                )
                msg = await self.build_outbound(tom)

                await self.connector.publish_outbound(msg)

                rmsg = turn_outbound_from_msg(msg)
                return rmsg
        except ApiError as e:
            logger.error(LOG_API_ERR, {"error": e.name, "message": str(e)})
            raise e

    async def build_outbound(self, tom: TurnOutboundMessage) -> Message:
        return tom.to_vumi(self.config.connector_name, self.config.transport_type)

    def _response(
        self,
        description: str,
        data: dict,
        status=HTTPStatus.OK,
    ) -> tuple[str, int, dict[str, str]]:
        headers = {"Content-Type": "application/json"}
        body = {
            "status": status.value,
            "code": status.phrase,
            "description": description,
            "result": data,
        }
        return json.dumps(body), status.value, headers
