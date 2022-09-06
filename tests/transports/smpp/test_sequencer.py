from vumi2.transports.smpp.sequencers import InMemorySequencer


async def test_in_memory_sequencer():
    """The allowed sequence_number range is from 0x00000001 to 0x7FFFFFFF"""
    sequencer = InMemorySequencer({})
    assert await sequencer.get_next_sequence_number() == 1
    assert await sequencer.get_next_sequence_number() == 2
    assert await sequencer.get_next_sequence_number() == 3
    # wrap around at 0xFFFFFFFF
    sequencer.sequence_number = 0x7FFFFFFE
    assert await sequencer.get_next_sequence_number() == 0x7FFFFFFF
    assert await sequencer.get_next_sequence_number() == 1
