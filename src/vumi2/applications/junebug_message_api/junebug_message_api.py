from logging import getLogger
from typing import Optional

from attrs import define, field
from httpx import AsyncClient

from vumi2.cli import class_from_string
from vumi2.messages import DeliveryStatus, Event, EventType, Message
from vumi2.workers import BaseConfig, BaseWorker

from . import junebug_state_cache

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


def junebug_from_msg(message: Message) -> dict:
    vumi_dict = message.serialise()
    msg = {
        "to": vumi_dict["to_addr"],
        "from": vumi_dict["from_addr"],
        "group": vumi_dict["group"],
        "message_id": vumi_dict["message_id"],
        "channel_id": vumi_dict["transport_name"],
        "timestamp": vumi_dict["timestamp"],
        "reply_to": vumi_dict["in_reply_to"],
        "content": vumi_dict["content"],
        "channel_data": vumi_dict["helper_metadata"],
    }
    msg["channel_data"]["session_event"] = vumi_dict["session_event"]
    return msg


def junebug_from_ev(event: Event, channel_id: str) -> dict:
    vumi_dict = event.serialise()
    ev = {
        "channel_id": channel_id,
        "timestamp": vumi_dict["timestamp"],
        "event_details": {},
        "event_type": None,
    }
    if event.event_type == EventType.ACK:
        ev["event_type"] = "submitted"
    elif event.event_type == EventType.NACK:
        ev["event_type"] = "rejected"
        ev["event_details"] = {"reason": event.nack_reason}
    elif event.event_type == EventType.DELIVERY_REPORT:
        ev["event_type"] = {
            DeliveryStatus.PENDING: "delivery_pending",
            DeliveryStatus.FAILED: "delivery_failed",
            DeliveryStatus.DELIVERED: "delivery_succeeded",
            None: None,
        }[event.delivery_status]
    return ev


@define(kw_only=True)
class JunebugMessageApiConfig(BaseConfig):
    connector_name: str

    # The URL to send HTTP POST requests to for MO messages
    mo_message_url: str

    # Authorization Token to use for the mo_message_url
    mo_message_url_auth_token: Optional[str] = None

    base_url_path: str = ""

    state_cache_class: str = f"{junebug_state_cache.__name__}.MemoryJunebugStateCache"
    state_cache_config: dict = field(factory=dict)

    # # Maximum time (seconds) a mo_message_url is allowed to take to process a message
    # mo_message_url_timeout: int = 10
    # # Maximum time (seconds) a mo_message_url is allowed to take to process an event
    # event_url_timeout: int = 10

    def url(self, path: str) -> str:
        return f"{self.base_url_path}{path}"


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
        # sm_url = self.config.url("/messages")
        # self.http_app.add_url_rule(sm_url, view_func=self.http_send_message)

    async def handle_inbound_message(self, message: Message) -> None:
        """
        Send the vumi message as an HTTP request to the configured URL.
        """
        logger.debug("Consuming inbound message %s", message)
        msg = junebug_from_msg(message)

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

        ev = junebug_from_ev(event, self.config.connector_name)

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

        # TODO: Look up message_id and post event.
