from datetime import datetime
from typing import Optional

from attrs import define
from cattrs import structure

from vumi2.messages import Message


class MessageCache:  # pragma: no cover
    def __init__(self, config: dict) -> None:
        ...

    async def store_outbound(self, outbound: Message) -> None:
        ...

    async def fetch_outbound(self, message_id: str) -> Optional[Message]:
        ...


@define
class MemoryMessageCacheConfig:
    timeout: int = 60 * 60


class MemoryMessageCache(MessageCache):
    def __init__(self, config: dict) -> None:
        self.config = structure(config, MemoryMessageCacheConfig)
        self.outbounds: dict[str, Message] = {}

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
