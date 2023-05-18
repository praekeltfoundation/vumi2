from typing import Optional

import cattrs
from attrs import define

from vumi2.message_caches import TimeoutDict


class BaseSmppCache:  # pragma: no cover
    def __init__(self, config: dict) -> None:
        ...

    async def store_multipart(
        self, ref_num: int, tot_num: int, part_num: int, content: str
    ) -> Optional[str]:
        ...

    async def store_smpp_message_id(
        self, vumi_message_id: str, smpp_message_id: str
    ) -> None:
        ...

    async def delete_smpp_message_id(self, smpp_message_id: str) -> None:
        ...

    async def get_smpp_message_id(self, smpp_message_id: str) -> Optional[str]:
        ...


@define
class InMemorySmppCacheConfig:
    timeout: int = 60 * 60 * 24


class InMemorySmppCache(BaseSmppCache):
    def __init__(self, config: dict) -> None:
        self.config = cattrs.structure(config, InMemorySmppCacheConfig)
        self._multipart: dict[tuple[int, int], dict[int, str]] = {}
        self._smpp_msg_id: TimeoutDict[str] = TimeoutDict(self.config.timeout)

    async def store_multipart(
        self, ref_num: int, tot_num: int, part_num: int, content: str
    ) -> Optional[str]:
        """
        Stores the one part of a multipart message in the cache. If this results in all
        the parts being stored in the cache, removes them from the cache and returns
        the joined content.
        """
        key = (ref_num, tot_num)
        parts = self._multipart.setdefault(key, {})
        parts[part_num] = content
        if len(parts) == tot_num:
            del self._multipart[key]
            return "".join(c for i, c in sorted(parts.items()))
        return None

    async def store_smpp_message_id(
        self, vumi_message_id: str, smpp_message_id: str
    ) -> None:
        """
        Stores the mapping between one or many smpp message IDs and the vumi message ID
        """
        self._smpp_msg_id[smpp_message_id] = vumi_message_id

    async def delete_smpp_message_id(self, smpp_message_id: str) -> None:
        """
        Removes the SMPP message ID from the cache
        """
        self._smpp_msg_id.pop(smpp_message_id, None)

    async def get_smpp_message_id(self, smpp_message_id: str) -> Optional[str]:
        """
        Returns the vumi message ID for the given smpp message id
        """
        return self._smpp_msg_id.pop(smpp_message_id)
