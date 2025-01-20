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

    * message: The message to send to Turn.

    Returns a dict like:
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
    msg = {
        "contact": {
            "id": message.to_addr,
            "profile": {"name": message.to_addr},
        },
        "message": {
            "type": "text",
            "text": {
                "body": message.content,
            },
            "from": message.from_addr,
            "id": message.message_id,
            "timestamp": str(int(message.timestamp.timestamp())),
        },
    }
    return msg


def turn_outbound_from_msg(message: Message) -> dict:
    """
    From https://whatsapp.turn.io/docs/api/channel_api#receiving-outbound-messages-from-your-channel

    * message: The message to send to the subscriber.

    Returns a dict like {"messages": [{"id": message.message_id}]}.
    """
    return {"messages": [{"id": message.message_id}]}


def turn_event_from_ev(event: Event) -> dict:
    """
    From https://whatsapp.turn.io/docs/api/channel_api#sending-outbound-message-status-to-your-channel

    * id (str) - The UUID of the message the event is for.
    * timestamp (str) - The timestamp at which the event occurred.
    * status (dict) - The status of the event. Currently only sent, delivered, and read
      are supported.
    """
    ev = {
        "id": event.user_message_id,
        "timestamp": str(int(event.timestamp.timestamp())),
        "status": None,
    }
    if event.event_type == EventType.DELIVERY_REPORT:
        ev["status"] = {
            DeliveryStatus.PENDING: "sent",
            DeliveryStatus.FAILED: "sent",
            DeliveryStatus.DELIVERED: "delivered",
            None: None,
        }[event.delivery_status]
    else:
        ev["status"] = "sent"
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
