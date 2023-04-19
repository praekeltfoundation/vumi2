from abc import ABC, abstractmethod
from typing import Optional


class JunebugStateCache(ABC):
    @abstractmethod
    async def fetch_event_url(self, message_id: str) -> Optional[str]:
        ...

    # TODO: More of this


class MemoryJunebugStateCache(JunebugStateCache):
    pass

    # TODO: More of this
