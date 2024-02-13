import json
from http import HTTPStatus
from logging import getLogger
from typing import Optional, Union

from attrs import define, field
from httpx import AsyncClient
from quart import request
from trio import move_on_after

from vumi2.cli import class_from_string
from vumi2.messages import (
    Event,
    Message,
    TransportType,
    generate_message_id,
)
from vumi2.workers import BaseConfig, BaseWorker

from . import junebug_state_cache
from .errors import JsonDecodeError, JunebugApiError, MessageNotFound
from .messages import (
    JunebugOutboundMessage,
    junebug_event_from_ev,
    junebug_inbound_from_msg,
    junebug_outbound_from_msg,
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
LOG_EV_URL_MISSING = "Cannot find event URL, missing user_message_id: %(event)s"

logger = getLogger(__name__)


@define(kw_only=True)
class JunebugMessageApiConfig(BaseConfig):
    # AMQP connector name. This is also used as `transport_name` for non-reply
    # outbound messages and `channel_id` for events.
    connector_name: str

    # The URL to POST inbound messages to.
    mo_message_url: str
    # Authorization token to use for inbound message HTTP requests.
    mo_message_url_auth_token: Optional[str] = None

    # The URL to POST events with no associated message info to.
    default_event_url: Optional[str] = None
    # Authorization token to use for events with no associates message info.
    default_event_auth_token: Optional[str] = None

    # Base URL path for outbound message HTTP requests. This has "/messages"
    # appended to it. For compatibility with existing Junebug API clients, set
    # it to "/channels/<channel_id>".
    base_url_path: str = ""

    # This is a required `Message` field, so we need to set it for non-reply
    # outbound messages.
    transport_type: TransportType = TransportType.SMS

    # If None, all outbound messages must be replies or have a from address.
    default_from_addr: Optional[str] = None

    # If True, outbound messages with both `to` and `reply_to` set will be sent
    # as non-reply messages if the `reply_to` message can't be found.
    allow_expired_replies: bool = False

    # State cache configuration. Currently limited to in-memory storage with a
    # configurable expiry time.
    state_cache_class: str = f"{junebug_state_cache.__name__}.MemoryJunebugStateCache"
    state_cache_config: dict = field(factory=dict)

    # Maximum time allowed (in seconds) for outbound message request handling.
    request_timeout: float = 4 * 60

    # Maximum time allowed (in seconds) for inbound message and event HTTP
    # requests.
    mo_message_url_timeout: float = 10
    event_url_timeout: float = 10

    def url(self, path: str) -> str:
        return "/".join([self.base_url_path.rstrip("/"), path.lstrip("/")])


class JunebugMessageApi(BaseWorker):
    """
    An implementation of the Junebug HTTP message API.

    Inbound messages and events are sent over HTTP to the configured
    URL(s). Outbound messages are received over HTTP and sent to the
    configured transport.
    """

    config: JunebugMessageApiConfig

    async def setup(self) -> None:
        state_cache_class = class_from_string(self.config.state_cache_class)
        self.state_cache = state_cache_class(self.config.state_cache_config)
        self.connector = await self.setup_receive_inbound_connector(
            self.config.connector_name,
            self.handle_inbound_message,
            self.handle_event,
        )
        self.http.app.add_url_rule(
            self.config.url("/messages"),
            view_func=self.http_send_message,
            methods=["POST"],
        )

        await self.start_consuming()

    async def handle_inbound_message(self, message: Message) -> None:
        """
        Send the vumi message as an HTTP request to the configured URL.
        """
        logger.debug("Consuming inbound message %s", message)
        msg = junebug_inbound_from_msg(message, message.transport_name)
        await self.state_cache.store_inbound(message)

        headers = {}
        if self.config.mo_message_url_auth_token is not None:
            headers["Authorization"] = f"Token {self.config.mo_message_url_auth_token}"

        timeout = self.config.mo_message_url_timeout
        with move_on_after(timeout) as cs:
            async with AsyncClient() as client:
                resp = await client.post(
                    self.config.mo_message_url,
                    json=msg,
                    headers=headers,
                )

            if resp.status_code < 200 or resp.status_code >= 300:
                logger.error(
                    LOG_MSG_HTTP_ERR,
                    {"code": resp.status_code, "body": resp.text, "message": msg},
                )

        if cs.cancelled_caught:
            logger.error(LOG_MSG_HTTP_TIMEOUT, {"timeout": timeout, "message": msg})

    async def handle_event(self, event: Event) -> None:
        """
        Send the vumi event as an HTTP request to the cached event URL
        for the associated outbound message.
        """
        logger.debug("Consuming event %s", event)
        event_hi = await self.state_cache.fetch_event_http_info(event.user_message_id)

        if event_hi is None:
            if self.config.default_event_url is None:
                logger.warning(LOG_EV_URL_MISSING, {"event": event})
                return
            # We have a default event URL and maybe an auth token too, so use it.
            event_hi = junebug_state_cache.EventHttpInfo(
                self.config.default_event_url, self.config.default_event_auth_token
            )

        ev = junebug_event_from_ev(event, self.config.connector_name)

        headers = {}
        if event_hi.auth_token is not None:
            headers["Authorization"] = f"Token {event_hi.auth_token}"

        timeout = self.config.event_url_timeout
        with move_on_after(timeout) as cs:
            async with AsyncClient() as client:
                resp = await client.post(event_hi.url, json=ev, headers=headers)

            if resp.status_code < 200 or resp.status_code >= 300:
                logger.error(
                    LOG_EV_HTTP_ERR,
                    {"code": resp.status_code, "body": resp.text, "event": ev},
                )

        if cs.cancelled_caught:
            logger.error(LOG_EV_HTTP_TIMEOUT, {"timeout": timeout, "event": ev})

    async def http_send_message(self) -> tuple[Union[str, dict], int, dict[str, str]]:
        _message_id = generate_message_id()
        try:
            # TODO: Log requests that timed out?
            with move_on_after(self.config.request_timeout):
                try:
                    msg_dict = json.loads(await request.get_data(as_text=True))
                except json.JSONDecodeError as e:
                    raise JsonDecodeError(str(e)) from e

                logger.debug("Received outbound message: %s", msg_dict)

                jom = JunebugOutboundMessage.deserialise(
                    msg_dict, default_from=self.config.default_from_addr
                )
                msg = await self.build_outbound(jom)

                if jom.event_url is not None:
                    await self.state_cache.store_event_http_info(
                        msg.message_id, jom.event_url, jom.event_auth_token
                    )

                await self.connector.publish_outbound(msg)

                # TODO: Special handling of outbound messages?
                rmsg = junebug_outbound_from_msg(msg, self.config.connector_name)
                return self._response("message submitted", rmsg, HTTPStatus.CREATED)
        except JunebugApiError as e:
            err = {"type": e.name, "message": str(e)}
            return self._response(e.description, {"errors": [err]}, e.status)

    async def build_outbound(self, jom: JunebugOutboundMessage) -> Message:
        if (msg_id := jom.reply_to) is not None:
            if (inbound := await self.state_cache.fetch_inbound(msg_id)) is not None:
                return jom.reply_to_vumi(inbound)
            if jom.to is None or not self.config.allow_expired_replies:
                raise MessageNotFound(f"Inbound message with id {msg_id} not found")
            # If we get here, we're allowed to send expired replies as new
            # messages and we have a to address to send this one to. That means
            # we can safely treat this as a non-reply.
        return jom.to_vumi(self.config.connector_name, self.config.transport_type)

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
