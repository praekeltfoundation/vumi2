from abc import ABC, abstractmethod
from collections.abc import Iterator, MutableMapping
from typing import Generic, Optional, TypeVar

import cattrs
from attrs import define
from trio import current_time

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

    def __getitem__(self, key: str) -> Optional[T]:
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


@define
class EventHttpInfo:
    url: str
    auth_token: Optional[str]


class JunebugStateCache(ABC):  # pragma: no cover
    @abstractmethod
    async def store_event_http_info(
        self,
        message_id: str,
        url: str,
        auth_token: Optional[str],
    ) -> None:
        ...

    @abstractmethod
    async def fetch_event_http_info(self, message_id: str) -> Optional[EventHttpInfo]:
        ...

    @abstractmethod
    async def delete_event_http_info(self, message_id: str) -> None:
        ...

    # TODO: More of this


@define
class MemoryJunebugStateCacheConfig:
    timeout: float = 60 * 60 * 24


class MemoryJunebugStateCache(JunebugStateCache):
    def __init__(self, config: dict) -> None:
        self.config = cattrs.structure(config, MemoryJunebugStateCacheConfig)
        timeout = self.config.timeout
        self._event_http_info: TimeoutDict[EventHttpInfo] = TimeoutDict(timeout)

    async def store_event_http_info(
        self,
        message_id: str,
        url: str,
        auth_token: Optional[str],
    ) -> None:
        """
        Stores the mapping between one or many smpp message IDs and the vumi message ID
        """
        self._event_http_info[message_id] = EventHttpInfo(url, auth_token)

    async def fetch_event_http_info(self, message_id: str) -> Optional[EventHttpInfo]:
        return self._event_http_info[message_id]

    async def delete_event_http_info(self, message_id: str) -> None:
        self._event_http_info.pop(message_id, None)

    # TODO: More of this
