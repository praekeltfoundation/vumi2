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


@define
class InMemorySmppCacheConfig:
    pass


class InMemorySmppCache(BaseSmppCache):
    def __init__(self, config: dict) -> None:
        self.config = cattrs.structure(config, InMemorySmppCacheConfig)
        self._multipart: Dict[Tuple[int, int], Dict[int, str]] = {}

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
