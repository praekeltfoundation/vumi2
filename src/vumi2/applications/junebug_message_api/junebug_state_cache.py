from abc import ABC, abstractmethod
from typing import Optional

import cattrs
from attrs import define

from vumi2.message_caches import TimeoutDict


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
