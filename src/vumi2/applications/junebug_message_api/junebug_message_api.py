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
from .errors import JsonDecodeError, JunebugApiError
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
LOG_EV_HTTP_ERR = (
    "Error sending event, received HTTP code %(code)s"
    " with body %(body)s. Event: %(event)s"
)
LOG_EV_URL_MISSING = "Cannot find event URL, missing user_message_id: %(event)s"

logger = getLogger(__name__)


@define(kw_only=True)
class JunebugMessageApiConfig(BaseConfig):
    connector_name: str

    # The URL to send HTTP POST requests to for MO messages
    mo_message_url: str

    # Authorization Token to use for the mo_message_url
    mo_message_url_auth_token: Optional[str] = None

    base_url_path: str = ""

    transport_type: TransportType = TransportType.SMS

    # If None, all outbound messages must be replies or have a from address
    default_from_addr: Optional[str] = None

    state_cache_class: str = f"{junebug_state_cache.__name__}.MemoryJunebugStateCache"
    state_cache_config: dict = field(factory=dict)

    # # Maximum time (seconds) a mo_message_url is allowed to take to process a message
    # mo_message_url_timeout: int = 10
    # # Maximum time (seconds) a mo_message_url is allowed to take to process an event
    # event_url_timeout: int = 10

    request_timeout: int = 4 * 60

    def url(self, path: str) -> str:
        return "/".join([self.base_url_path.rstrip("/"), path.lstrip("/")])


class JunebugMessageApi(BaseWorker):
    """
    An implementation of the Junebug HTTP message API.

    Inbound messages and events are sent over HTTP to the configured
    URL(s). Outbound messages are received over HTTP and sent to the
    configured transport.

    TODO: Finish implementation.
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

    async def handle_inbound_message(self, message: Message) -> None:
        """
        Send the vumi message as an HTTP request to the configured URL.
        """
        logger.debug("Consuming inbound message %s", message)
        msg = junebug_inbound_from_msg(message, message.transport_name)

        headers = {}
        if self.config.mo_message_url_auth_token is not None:
            headers["Authorization"] = f"Token {self.config.mo_message_url_auth_token}"

        # TODO: Handle timeouts
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

    async def handle_event(self, event: Event) -> None:
        logger.debug("Consuming event %s", event)
        event_hi = await self.state_cache.fetch_event_http_info(event.user_message_id)

        if event_hi is None:
            logger.warning(LOG_EV_URL_MISSING, {"event": event})
            return

        ev = junebug_event_from_ev(event, self.config.connector_name)

        headers = {}
        if event_hi.auth_token is not None:
            headers["Authorization"] = f"Token {event_hi.auth_token}"

        # TODO: Handle timeouts
        async with AsyncClient() as client:
            resp = await client.post(event_hi.url, json=ev, headers=headers)

        if resp.status_code < 200 or resp.status_code >= 300:
            logger.error(
                LOG_EV_HTTP_ERR,
                {"code": resp.status_code, "body": resp.text, "event": ev},
            )

    async def http_send_message(self) -> tuple[Union[str, dict], int, dict[str, str]]:
        _message_id = generate_message_id()
        try:
            with move_on_after(self.config.request_timeout):
                try:
                    msg_dict = json.loads(await request.get_data(as_text=True))
                except json.JSONDecodeError as e:
                    raise JsonDecodeError(str(e)) from e

                jom = JunebugOutboundMessage.deserialise(
                    msg_dict, default_from=self.config.default_from_addr
                )
                # TODO: Look up reply_to info.
                msg = jom.to_vumi(
                    self.config.connector_name, self.config.transport_type
                )

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
