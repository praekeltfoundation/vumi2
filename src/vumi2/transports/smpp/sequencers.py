# TODO: Sequencers that can be shared across processes


class Sequencer:  # pragma: no cover
    def __init__(self, config: dict):
        ...

    async def get_next_sequence_number(self) -> int:
        ...


class InMemorySequencer(Sequencer):
    def __init__(self, config: dict):
        self.config = config
        self.sequence_number = 0

    async def get_next_sequence_number(self) -> int:
        """The allowed sequence_number range is from 0x00000001 to 0x7FFFFFFF"""
        self.sequence_number = (self.sequence_number % 0x7FFFFFFF) + 1
        return self.sequence_number
