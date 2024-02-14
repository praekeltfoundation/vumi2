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

from .errors import ApiUsageError, InvalidBody


def junebug_inbound_from_msg(message: Message, channel_id: str) -> dict:
    """
    From https://junebug.readthedocs.io/en/latest/http_api.html

    * to (str) – The address that the message was sent to.
    * from (str) – The address that the message was sent from.
    * group (str) – If the transport supports groups, the group that the message
      was sent in.
    * message_id (str) – The string representation of the UUID of the message.
    * channel_id (str) – The string representation of the UUID of the channel
      that the message came in on.
    * timestamp (str) – The timestamp of when the message arrived at the channel,
      in the format "%Y-%m-%d %H:%M:%S.%f.
    * reply_to (str) – If this message is a reply of an outbound message, the
      string representation of the UUID of the outbound message.
    * content (str) – The text content of the message.
    * channel_data (dict) – Any channel implementation specific data. The
      contents of this differs between channel implementations.
    """
    vumi_dict = message.serialise()
    msg = {
        "to": vumi_dict["to_addr"],
        "from": vumi_dict["from_addr"],
        "group": vumi_dict["group"],
        "message_id": vumi_dict["message_id"],
        "channel_id": channel_id,
        "timestamp": vumi_dict["timestamp"],
        "reply_to": vumi_dict["in_reply_to"],
        "content": vumi_dict["content"],
        "channel_data": vumi_dict["helper_metadata"],
    }
    if vumi_dict["session_event"] is not None:
        msg["channel_data"]["session_event"] = vumi_dict["session_event"]
    return msg


# We currently use the inbound message code for building outbound message
# responses (because the format of a junebug outbound message is different),
# but that may change in the future.
junebug_outbound_from_msg = junebug_inbound_from_msg


def junebug_event_from_ev(event: Event, channel_id: str) -> dict:
    """
    From https://junebug.readthedocs.io/en/latest/http_api.html

    * event_type (str) – The type of the event. See the list of event
      types below.
    * message_id (str) – The UUID of the message the event is for.
    * channel_id (str) – The UUID of the channel the event occurred for.
    * timestamp (str) – The timestamp at which the event occurred.
    * event_details (dict) – Details specific to the event type.
    """
    vumi_dict = event.serialise()
    ev = {
        "message_id": vumi_dict["user_message_id"],
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


@define
class JunebugOutboundMessage:
    """
    From https://junebug.readthedocs.io/en/latest/http_api.html

    * to (str) – the address (e.g. MSISDN) to send the message too. If
      Junebug is configured with allow_expired_replies The to parameter
      is used as a fallback in case the value of the reply_to parameter
      does not resolve to an inbound message.
    * from (str) – the address the message is from. May be null if the
      channel only supports a single from address.
    * group (str) – If supported by the channel, the group to send the
      messages to. Not required, and may be null
    * reply_to (str) – the uuid of the message being replied to if this
      is a response to a previous message. Important for session-based
      transports like USSD. Optional. Can be combined with to and from
      if Junebug is configured with allow_expired_replies. If that is
      the case the to and from values will be used as a fallback in case
      the value of the reply_to parameter does not resolve to an inbound
      message. The default settings allow 10 minutes to reply to a
      message, after which an error will be returned.
    * content (str) – The text content of the message. Required.
    * event_url (str) – URL to call for status events (e.g.
      acknowledgements and delivery reports) related to this message.
      The default settings allow 2 days for events to arrive, after
      which they will no longer be forwarded.
    * event_auth_token (str) – The token to use for authentication if
      the event_url requires token auth.
    * priority (int) – Delivery priority from 1 to 5. Higher priority
      messages are delivered first. If omitted, priority is 1. Not yet
      implemented.
    * channel_data (dict) – Additional data that is passed to the
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
    ) -> "JunebugOutboundMessage":
        if data.get("from") is None:
            data["from"] = default_from
        try:
            return cattrs.structure(data, cls)
        except cattrs.BaseValidationError as bve:
            # cattrs collects all validation errors in an exception group, even
            # if we only have one. Ideally we'd report everything it gives us,
            # but for not let's assume there's only one and reraise that. At
            # the same time, we translate cattrs validation errors into the
            # format we expect the API to return.
            try:
                raise bve.exceptions[0] from bve
            except cattrs.ForbiddenExtraKeysError as feke:
                fmsg = f"u'{list(feke.extra_fields)[0]}' was unexpected"
                errmsg = f"Additional properties are not allowed ({fmsg})"
                raise InvalidBody(errmsg) from bve

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
    JunebugOutboundMessage,
    cattrs.global_converter,
    from_addr=override(rename="from"),
    _cattrs_forbid_extra_keys=True,
)
cattrs.register_structure_hook(JunebugOutboundMessage, st_hook)
