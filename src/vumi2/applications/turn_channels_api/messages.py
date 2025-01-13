from typing import Any

import cattrs
from attrs import define, field
from cattrs.gen import make_dict_structure_fn, override

from vumi2.messages import (
    DeliveryStatus,
    Event,
    EventType,
    Message,
    TransportType,
)

from ..errors import ApiUsageError


def turn_inbound_from_msg(message: Message, channel_id: str) -> dict:
    """
    From
    https://whatsapp.turn.io/docs/api/channel_api#sending-inbound-messages-to-your-channel

    * contact:
      * id: (str) The Turn contact ID
      * profile:
        * name: (str) The contact's name
    * message:
      * type: text, video, audio, button, image, document, sticker or interactive
      * text: When the message type is text, this must be included
        * body: (str) The text of the message
      * from: (str) The user ID. A Channel can respond to a user using this ID.
      * id: (str) The ID for the message that was received by the Channel.
      * timestamp: (int) Unix timestamp indicating when received the message
      from the user.
    """
    turn_dict = message.serialise()
    msg = {
        "contact": {
            "id": turn_dict["to_addr"],
            "profile": {"name": turn_dict["to_addr"]},
        },
        "message": {
            "type": "text",
            "text": {
                "body": turn_dict["content"],
            },
            "from": turn_dict["from_addr"],
            "id": turn_dict["message_id"],
            "timestamp": turn_dict["timestamp"],
        },
    }
    return msg


# We currently use the inbound message code for building outbound message
# responses (because the format of a junebug outbound message is different),
# but that may change in the future.
turn_outbound_from_msg = turn_inbound_from_msg


def turn_event_from_ev(event: Event, channel_id: str) -> dict:
    """
    From https://junebug.readthedocs.io/en/latest/http_api.html

    * event_type (str) - The type of the event. See the list of event
      types below.
    * message_id (str) - The UUID of the message the event is for.
    * timestamp (str) - The timestamp at which the event occurred.
    * event_details (dict) - Details specific to the event type.
    """
    turn_dict = event.serialise()
    ev = {
        "id": turn_dict["user_message_id"],
        "channel_id": channel_id,
        "timestamp": turn_dict["timestamp"],
        "event_details": {},
        "event_type": "sent",
    }
    if event.event_type == EventType.NACK:
        ev["event_details"] = {"reason": event.nack_reason}
    elif event.event_type == EventType.DELIVERY_REPORT:
        ev["event_type"] = {
            DeliveryStatus.PENDING: "sent",
            DeliveryStatus.FAILED: "sent",
            DeliveryStatus.DELIVERED: "delivered",
            None: None,
        }[event.delivery_status]
    return ev


@define
class TurnOutboundMessage:
    """
    From
    https://whatsapp.turn.io/docs/api/channel_api#receiving-outbound-messages-from-your-channel

    * to (str) - the address (e.g. MSISDN) to send the message to.
    * from (str) - the address the message is from. May be null if the
      channel only supports a single from address.
    * group (str) - If supported by the channel, the group to send the
      messages to. Not required, and may be null
    * reply_to (str) - the uuid of the message being replied to if this
      is a response to a previous message. Important for session-based
      transports like USSD. Optional.
      The default settings allow 10 minutes to reply to a
      message, after which an error will be returned.
    * content (str) - The text content of the message. Required.
    * event_url (str) - URL to call for status events (e.g.
      acknowledgements and delivery reports) related to this message.
      The default settings allow 2 days for events to arrive, after
      which they will no longer be forwarded.
    * event_auth_token (str) - The token to use for authentication if
      the event_url requires token auth.
    * priority (int) - Delivery priority from 1 to 5. Higher priority
      messages are delivered first. If omitted, priority is 1. Not yet
      implemented.
    * channel_data (dict) - Additional data that is passed to the
      channel to interpret. E.g. continue_session for USSD,
      direct_message or tweet for Twitter.
    """

    content: str
    to: str | None = None
    from_addr: str | None = None
    group: str | None = None
    reply_to: str | None = None
    event_url: str | None = None
    event_auth_token: str | None = None
    priority: int = 1
    channel_data: dict[str, Any] = field(factory=dict)

    def __attrs_post_init__(self):
        if self.to is None and self.reply_to is None:
            raise ApiUsageError('Either "to" or "reply_to" must be specified')

    @classmethod
    def deserialise(
        cls, data: dict[str, Any], default_from: str | None = None
    ) -> "TurnOutboundMessage":
        for key in ["context", "turn", "to"]:
            if key not in data:
                raise ApiUsageError(f"Missing key: {key}")
        if "contact" not in data["context"]:
            raise ApiUsageError("Missing key: context.contact")
        if "groups" not in data["context"]["contact"]:
            raise ApiUsageError("Missing key: context.contact.groups")
        if "text" not in data["turn"]:
            raise ApiUsageError("Missing key: turn.text")
        if "body" not in data["turn"]["text"]:
            raise ApiUsageError("Missing key: turn.text.body")
        contact = data["context"]["contact"]
        return TurnOutboundMessage(
            content=data["turn"]["text"]["body"],
            to=data["to"],
            from_addr=default_from,
            group=contact["groups"][0]["name"],
            reply_to=default_from,
            event_url=data.get("event_url"),
            event_auth_token=data.get("event_auth_token"),
            priority=1,
            channel_data={},  # TODO
        )

    def _shared_vumi_fields(self):
        helper_metadata: dict[str, Any] = {**self.channel_data}
        fields = {"helper_metadata": helper_metadata}

        for cd_field in ["continue_session", "session_event"]:
            if (value := helper_metadata.pop(cd_field, None)) is not None:
                fields[cd_field] = value

        return fields

    def to_vumi(self, transport_name: str, transport_type: TransportType) -> Message:
        """
        Build a vumi outbound message that isn't a reply.

        The caller must ensure that both `to` and `from_addr` are not None.
        """
        message = {
            "to_addr": self.to,
            "from_addr": self.from_addr,
            "group": self.group,
            "content": self.content,
            "transport_name": transport_name,
            "transport_type": transport_type,
            **self._shared_vumi_fields(),
        }

        return Message.deserialise(message)

    def reply_to_vumi(self, in_msg: Message) -> Message:
        """
        Build a vumi outbound message that's a reply to the given inbound message.
        """
        return in_msg.reply(self.content, **self._shared_vumi_fields())


st_hook = make_dict_structure_fn(
    TurnOutboundMessage,
    cattrs.global_converter,
    from_addr=override(rename="from"),
    _cattrs_forbid_extra_keys=True,
)
cattrs.register_structure_hook(TurnOutboundMessage, st_hook)
