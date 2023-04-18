from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Type, Union
from uuid import uuid4

import cattrs
from attrs import Factory, define, field

VUMI_DATE_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def deserialise_vumi_timestamp(value: str, _: Any) -> datetime:
    if "." not in value[-10:]:
        value = f"{value}.0"
    return datetime.strptime(value, VUMI_DATE_FORMAT)


def serialise_vumi_timestamp(value: datetime) -> str:
    return value.strftime(VUMI_DATE_FORMAT)


cattrs.register_structure_hook(datetime, deserialise_vumi_timestamp)
cattrs.register_unstructure_hook(datetime, serialise_vumi_timestamp)


def generate_message_id() -> str:
    return uuid4().hex


class Session(Enum):
    NONE = None
    NEW = "new"
    RESUME = "resume"
    CLOSE = "close"


class TransportType(Enum):
    HTTP_API = "http_api"
    IRC = "irc"
    TELNET = "telnet"
    TWITTER = "twitter"
    SMS = "sms"
    USSD = "ussd"
    XMPP = "xmpp"
    MXIT = "mxit"
    WECHAT = "wechat"


class AddressType(Enum):
    IRC_NICKNAME = "irc_nickname"
    TWITTER_HANDLE = "twitter_handle"
    MSISDN = "msisdn"
    GTALK_ID = "gtalk_id"
    JABBER_ID = "jabber_id"
    MXIT_ID = "mxit_id"
    WECHAT_ID = "wechat_id"


class EventType(Enum):
    ACK = "ack"
    NACK = "nack"
    DELIVERY_REPORT = "delivery_report"


class DeliveryStatus(Enum):
    PENDING = "pending"
    FAILED = "failed"
    DELIVERED = "delivered"


@define
class Message:
    to_addr: str
    from_addr: str
    transport_name: str
    transport_type: TransportType
    message_version: str = "20110921"
    message_type: str = "user_message"
    timestamp: datetime = Factory(datetime.utcnow)
    routing_metadata: dict = Factory(dict)
    helper_metadata: dict = Factory(dict)
    message_id: str = Factory(generate_message_id)
    in_reply_to: Optional[str] = None
    provider: Optional[str] = None
    session_event: Session = Session.NONE
    content: Optional[str] = None
    transport_metadata: dict = Factory(dict)
    group: Optional[str] = None
    to_addr_type: Optional[AddressType] = None
    from_addr_type: Optional[AddressType] = None

    def serialise(self) -> Dict[str, Any]:
        return cattrs.unstructure(self)

    @classmethod
    def deserialise(cls: "Type[Message]", data: Dict[str, Any]) -> "Message":
        return cattrs.structure(data, cls)

    def reply(
        self, content: Optional[str] = None, session_event=Session.RESUME, **kwargs
    ) -> "Message":
        for f in [
            "to_addr",
            "from_addr",
            "group",
            "in_reply_to",
            "providertransport_name",
            "transport_type",
            "transport_metadata",
        ]:
            if f in kwargs:
                # Other "bad keyword argument" conditions cause TypeErrors.
                raise TypeError(f"'{f}' may not be overridden.")

        fields = {
            "content": content,
            "session_event": session_event,
            "to_addr": self.from_addr,
            "from_addr": self.to_addr,
            "group": self.group,
            "in_reply_to": self.message_id,
            "provider": self.provider,
            "transport_name": self.transport_name,
            "transport_type": self.transport_type,
            "transport_metadata": self.transport_metadata,
        }
        fields.update(kwargs)

        return Message(**fields)


@define
class Event:
    user_message_id: str
    event_type: EventType = field()
    message_version: str = "20110921"
    message_type: str = "event"
    timestamp: datetime = Factory(datetime.utcnow)
    routing_metadata: dict = Factory(dict)
    helper_metadata: dict = Factory(dict)
    transport_metadata: Optional[dict] = Factory(dict)
    event_id: str = Factory(generate_message_id)
    sent_message_id: Optional[str] = None
    nack_reason: Optional[str] = None
    delivery_status: Optional[DeliveryStatus] = None

    @event_type.validator
    def _check_event_type(self, _, value: EventType) -> None:
        if value == EventType.ACK:
            if self.sent_message_id is None:
                raise ValueError("sent_message_id cannot be null for ack event type")
        elif value == EventType.NACK:
            if self.nack_reason is None:
                raise ValueError("nack_reason cannot be null for nack event type")
        elif value == EventType.DELIVERY_REPORT:
            if self.delivery_status is None:
                raise ValueError(
                    "delivery_status cannot be null for delivery_report event type"
                )
        else:
            # Empty else clause so the linter doesn't complain about nested `if`s.
            pass

    def serialise(self) -> Dict[str, Any]:
        return cattrs.unstructure(self)

    @classmethod
    def deserialise(cls: "Type[Event]", data: Dict[str, Any]) -> "Event":
        return cattrs.structure(data, cls)


MessageType = Union[Message, Event]
