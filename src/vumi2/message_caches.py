from abc import ABC, abstractmethod
from collections.abc import Iterator, MutableMapping
from typing import Generic, TypeVar

from attrs import define
from cattrs import structure
from trio import current_time

from vumi2.messages import Message

T = TypeVar("T")


class TimeoutDict(MutableMapping, Generic[T]):
    def __init__(self, timeout: float):
        self.data: dict[str, tuple[float, T]] = {}
        self.timeout = timeout

    def _remove_expired(self):
        now = current_time()
        to_remove = []
        for key, (timestamp, _) in self.data.items():
            if (now - timestamp) > self.timeout:
                to_remove.append(key)
            else:
                # Dictionaries are ordered, so we don't need to check all the
                # others if we've found a new enough timestamp.
                break
        for key in to_remove:
            self.data.pop(key, None)

    def __setitem__(self, key: str, value: T) -> None:
        self.data.pop(key, None)
        self._remove_expired()
        self.data[key] = (current_time(), value)

    def __getitem__(self, key: str) -> T | None:
        self._remove_expired()
        if key not in self.data:
            return None
        return self.data[key][1]

    def __delitem__(self, key: str) -> None:
        self.data.pop(key, None)
        self._remove_expired()

    def __iter__(self) -> Iterator[str]:
        self._remove_expired()
        return iter(self.data)

    def __len__(self) -> int:
        self._remove_expired()
        return len(self.data)


class MessageCache(ABC):  # pragma: no cover
    @abstractmethod
    def __init__(self, config: dict) -> None:
        ...

    @abstractmethod
    async def store_outbound(self, outbound: Message) -> None:
        ...

    @abstractmethod
    async def fetch_outbound(self, message_id: str) -> Message | None:
        ...

    @abstractmethod
    async def store_inbound(self, inbound: Message) -> None:
        ...

    @abstractmethod
    async def fetch_inbound(self, message_id: str) -> Message | None:
        ...

    @abstractmethod
    async def fetch_last_inbound_by_from_address(
        self, from_addr: str
    ) -> Message | None:
        ...


@define
class MemoryMessageCacheConfig:
    timeout: int = 60 * 60


class MemoryMessageCache(MessageCache):
    def __init__(self, config: dict) -> None:
        self.config = structure(config, MemoryMessageCacheConfig)
        timeout = self.config.timeout
        self._outbounds: TimeoutDict[Message] = TimeoutDict(timeout)
        self._inbounds: TimeoutDict[Message] = TimeoutDict(timeout)

    async def store_outbound(self, outbound: Message) -> None:
        self._outbounds[outbound.message_id] = outbound

    async def fetch_outbound(self, message_id: str) -> Message | None:
        return self._outbounds.get(message_id)

    async def store_inbound(self, inbound: Message) -> None:
        self._inbounds[inbound.message_id] = inbound

    async def fetch_inbound(self, message_id: str) -> Message | None:
        return self._inbounds.get(message_id)

    async def fetch_last_inbound_by_from_address(
        self, from_addr: str
    ) -> Message | None:
        messages = [
            msg
            for msg in self._inbounds.values()
            if msg is not None and msg.from_addr == from_addr
        ]
        if not messages:
            return None
        return max(messages, key=lambda x: x.timestamp)
