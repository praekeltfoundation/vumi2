from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

import cattrs
from attrs import Factory, define

VUMI_DATE_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
_VUMI_DATE_FORMAT_NO_MICROSECONDS = "%Y-%m-%d %H:%M:%S"


def deserialise_vumi_timestamp(value: str, _: Any) -> datetime:
    date_format = VUMI_DATE_FORMAT
    if "." not in value[-10:]:
        date_format = _VUMI_DATE_FORMAT_NO_MICROSECONDS
    return datetime.strptime(value, date_format)


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

    def serialise(self):
        return cattrs.unstructure(self)

    @classmethod
    def deserialise(cls, data):
        return cattrs.structure(data, cls)
