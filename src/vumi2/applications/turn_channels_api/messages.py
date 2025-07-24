from typing import Any

import cattrs
from attrs import define, field
from cattrs.gen import make_dict_structure_fn, override

from vumi2.messages import (
    DeliveryStatus,
    Event,
    EventType,
    Message,
    Session,
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
            "id": message.from_addr,
            "profile": {"name": message.from_addr},
        },
        "message": {
            "type": "text",
            "text": {
                # Default to "hi" if content is empty,
                # which happens at the start of a USSD session.
                # Turn doesn't allow empty messages.
                "body": message.content or "hi",
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


def turn_event_from_ev(event: Event, outbound: Message) -> dict:
    """
    From https://whatsapp.turn.io/docs/api/channel_api#sending-outbound-message-status-to-your-channel

    * id (str) - The UUID of the message the event is for.
    * timestamp (str) - The timestamp at which the event occurred.
    * status (dict) - The status of the event. Currently only sent, delivered, and read
      are supported.
    * recipient_id (str) - The UUID of the message the event is for.
    """
    ev = {
        "status": {
            "id": event.user_message_id,
            "timestamp": str(int(event.timestamp.timestamp())),
            "status": None,
            "recipient_id": outbound.to_addr,
        }
    }
    if event.event_type == EventType.DELIVERY_REPORT:
        ev["status"]["status"] = {
            DeliveryStatus.PENDING: "sent",
            # TODO: Turn doesn't accept a failed status. Log the failure for now?
            DeliveryStatus.FAILED: "sent",
            DeliveryStatus.DELIVERED: "delivered",
            None: None,
        }[event.delivery_status]
    else:
        ev["status"]["status"] = "sent"
    return ev


@define
class TurnOutboundMessage:
    """
    From
    https://whatsapp.turn.io/docs/api/channel_api#receiving-outbound-messages-from-your-channel

    * to (str) - the address (e.g. MSISDN) to send the message to.
    * from (str) - the address the message is from. May be null if the
      channel only supports a single from address.
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
    reply_to: str | None = None
    priority: int = 1
    waiting_for_user_input: bool = False
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
        # if "contact" not in data["context"]:
        #     raise ApiUsageError("Missing key: context.contact")
        if "text" not in data["turn"]:
            raise ApiUsageError("Missing key: turn.text")
        if "body" not in data["turn"]["text"]:
            raise ApiUsageError("Missing key: turn.text.body")
        return TurnOutboundMessage(
            content=data["turn"]["text"]["body"],
            to=data["to"],
            from_addr=default_from,
            reply_to=data.get("reply_to", ""),
            priority=1,
            waiting_for_user_input=data.get("waiting_for_user_input", False),
            channel_data={},
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
            "content": self.content,
            "transport_name": transport_name,
            "transport_type": transport_type,
            "session_event": Session.RESUME
            if self.waiting_for_user_input
            else Session.CLOSE,
            **self._shared_vumi_fields(),
        }

        return Message.deserialise(message)

    def reply_to_vumi(self, in_msg: Message) -> Message:
        """
        Build a vumi outbound message that's a reply to the given inbound message.
        """
        session_event = Session.RESUME if self.waiting_for_user_input else Session.CLOSE
        return in_msg.reply(
            self.content, session_event=session_event, **self._shared_vumi_fields()
        )


st_hook = make_dict_structure_fn(
    TurnOutboundMessage,
    cattrs.global_converter,
    from_addr=override(rename="from"),
    _cattrs_forbid_extra_keys=True,
)
cattrs.register_structure_hook(TurnOutboundMessage, st_hook)
