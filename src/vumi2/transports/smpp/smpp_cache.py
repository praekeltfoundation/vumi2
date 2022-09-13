from datetime import datetime
from typing import Dict, Optional, Tuple

import cattrs
from attrs import define


class BaseSmppCache:  # pragma: no cover
    def __init__(self, config: dict) -> None:
        ...

    async def store_multipart(
        self, ref_num: int, tot_num: int, part_num: int, content: str
    ) -> Optional[str]:
        ...

    async def store_smpp_message_id(
        self, num_parts: int, vumi_message_id: str, smpp_message_id: str
    ) -> None:
        ...

    async def delete_smpp_message_id(self, smpp_message_id: str):
        ...

    async def seen_success_delivery_report(self, smpp_message_id) -> bool:
        ...


@define
class InMemorySmppCacheConfig:
    timeout: int = 60 * 60 * 24


class InMemorySmppCache(BaseSmppCache):
    def __init__(self, config: dict) -> None:
        self.config = cattrs.structure(config, InMemorySmppCacheConfig)
        self._multipart: Dict[Tuple[int, int], Dict[int, str]] = {}
        self._smpp_msg_id: Dict[str, Tuple[str, int, datetime]] = {}
        self._vumi_msg_dr: Dict[str, Dict[str, bool]] = {}

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

    async def _remove_expired(self):
        now = datetime.now()
        to_remove = []
        for key, (_, _, timestamp) in self._smpp_msg_id.items():
            if (now - timestamp).total_seconds() >= self.config.timeout:
                to_remove.append(key)
            else:
                # Dictionaries are ordered, so we don't need to check all the others
                # if we've found a new enough message
                break
        for key in to_remove:
            await self.delete_smpp_message_id(key)

    async def store_smpp_message_id(
        self, num_parts: int, vumi_message_id: str, smpp_message_id: str
    ) -> None:
        """
        Stores the mapping between one or many smpp message IDs and the vumi message ID
        """
        await self._remove_expired()
        self._smpp_msg_id[smpp_message_id] = (
            vumi_message_id,
            num_parts,
            datetime.now(),
        )
        self._vumi_msg_dr.setdefault(vumi_message_id, {})[smpp_message_id] = False

    async def delete_smpp_message_id(self, smpp_message_id: str):
        """
        Removes the SMPP message ID from the cache, and all other related message ids
        for that message
        """
        try:
            (vumi_msg_id, _, _) = self._smpp_msg_id.pop(smpp_message_id)
            vumi_msg_dr = self._vumi_msg_dr.pop(vumi_msg_id)
            for smpp_msg_id in vumi_msg_dr.keys():
                self._smpp_msg_id.pop(smpp_msg_id, None)
        except KeyError:
            return

    async def seen_success_delivery_report(self, smpp_message_id) -> bool:
        """
        Called when we've received a success delivery report for this message_id.

        Returns True and removes from the cache if we've have success delivery reports
        for all of the message parts, or False if we haven't
        """
        await self._remove_expired()
        try:
            vumi_message_id, num_parts, _ = self._smpp_msg_id[smpp_message_id]
            parts = self._vumi_msg_dr[vumi_message_id]
            parts[smpp_message_id] = True
            if sum(1 for v in parts.values() if v) == num_parts:
                await self.delete_smpp_message_id(smpp_message_id)
                return True
        except KeyError:
            pass
        return False
