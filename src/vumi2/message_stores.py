from datetime import datetime
from typing import Dict, Optional, Type

from attrs import define
from cattrs import structure
from typing_extensions import Protocol

from vumi2.messages import Message


class MessageStoreConfig(Protocol):  # pragma: no cover
    @classmethod
    def deserialise(
        cls: "Type[MessageStoreConfig]", config: dict
    ) -> "MessageStoreConfig":
        ...


class MessageStore(Protocol):  # pragma: no cover
    CONFIG_CLASS: Type[MessageStoreConfig]

    def __init__(self, config: MessageStoreConfig) -> None:
        ...

    async def store_outbound(self, outbound: Message) -> None:
        ...

    async def fetch_outbound(self, message_id: str) -> Optional[Message]:
        ...


@define
class MemoryMessageStoreConfig:
    timeout: int = 60 * 60

    @classmethod
    def deserialise(cls, config: dict) -> "MemoryMessageStoreConfig":
        return structure(config, cls)


class MemoryMessageStore:
    CONFIG_CLASS = MemoryMessageStoreConfig

    def __init__(self, config: MemoryMessageStoreConfig) -> None:
        self.config = config
        self.outbounds: Dict[str, Message] = {}

    def _remove_expired(self) -> None:
        timestamp = datetime.utcnow()
        expired_outbounds = []
        for message_id, message in self.outbounds.items():
            if (timestamp - message.timestamp).total_seconds() >= self.config.timeout:
                expired_outbounds.append(message_id)
            else:
                # Dictionaries are ordered, so we don't need to check all the others
                # if we've found a new enough message
                break
        for message_id in expired_outbounds:
            del self.outbounds[message_id]

    async def store_outbound(self, outbound: Message) -> None:
        self._remove_expired()
        self.outbounds[outbound.message_id] = outbound

    async def fetch_outbound(self, message_id: str) -> Optional[Message]:
        self._remove_expired()
        return self.outbounds.get(message_id)
