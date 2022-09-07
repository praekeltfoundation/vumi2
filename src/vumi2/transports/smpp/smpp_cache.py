from typing import Dict, Optional


class SmppCache:  # pragma: no cover
    def __init__(self, config: dict) -> None:
        ...

    async def store_sequence_number_to_message_id(
        self, sequence_number: int, message_id: str
    ) -> None:
        """
        Stores a vumi message ID, to be fetched later by the SMPP sequence number
        """
        ...

    async def fetch_message_id_from_sequence_number(
        self, sequence_number: int
    ) -> Optional[str]:
        """
        Fetches a vumi message ID, according to the SMPP sequence number, and then
        deletes it from the cache. Returns None if no message id is found for the
        sequence number
        """
        ...


class InMemorySmppCache(SmppCache):
    def __init__(self, config: dict) -> None:
        self.config = config
        self._seq_num_to_msg_id: Dict[int, str] = {}

    async def store_sequence_number_to_message_id(
        self, sequence_number: int, message_id: str
    ) -> None:
        self._seq_num_to_msg_id[sequence_number] = message_id

    async def fetch_message_id_from_sequence_number(
        self, sequence_number: int
    ) -> Optional[str]:
        return self._seq_num_to_msg_id.pop(sequence_number, None)
