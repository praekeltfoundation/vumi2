import pytest
from smpp.pdu.pdu_types import (
    AddrNpi,
    AddrTon,
    DataCoding,
    DataCodingDefault,
    RegisteredDelivery,
    RegisteredDeliveryReceipt,
    RegisteredDeliverySmeOriginatedAcks,
)

from vumi2.messages import Message, TransportType
from vumi2.transports.smpp.processors import SubmitShortMessageProcessor
from vumi2.transports.smpp.sequencers import InMemorySequencer


@pytest.fixture
async def sequencer() -> InMemorySequencer:
    return InMemorySequencer({})


@pytest.fixture
async def submit_sm_processor(
    sequencer: InMemorySequencer,
) -> SubmitShortMessageProcessor:
    return SubmitShortMessageProcessor({}, sequencer)


async def test_submit_sm_outbound_vumi_message(
    submit_sm_processor: SubmitShortMessageProcessor,
):
    """
    Creates a valid PDU representing the outbound vumi message
    """
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content='Knights who say "Nì!"',
    )
    [pdu] = await submit_sm_processor.handle_outbound_message(message)
    assert pdu.params["source_addr"] == b"12345"
    assert pdu.params["destination_addr"] == b"+27820001001"
    assert pdu.params["short_message"] == b'Knights who say "N\x07!"'


@pytest.fixture
async def submit_sm_processor_custom_config(
    sequencer: InMemorySequencer,
) -> SubmitShortMessageProcessor:
    # TODO: Better config than this
    # Because the PDU library generates Enums that look like:
    # class AddrTon(Enum):
    #     UNKNOWN = 1
    #     INTERNATIONAL = 2
    #     ...
    # we have to specify those numbers here when defining config. This makes the config
    # unclear, but also makes it really easy to make mistakes, since these are all
    # off-by-one compared to the spec; eg. a data_coding of 1 represents
    # SMSC_DEFAULT_ALPHABET, which is 0x00 in the SMPP spec
    return SubmitShortMessageProcessor(
        {
            "data_coding": 2,
            "multipart_handling": "message_payload",
            "service_type": "test service type",
            "source_addr_ton": 5,
            "source_addr_npi": 6,
            "dest_addr_ton": 2,
            "dest_addr_npi": 4,
            "registered_delivery": {
                "delivery_receipt": 2,
                "sme_originated_acks": [2],
                "intermediate_notification": True,
            },
        },
        sequencer,
    )


async def test_submit_sm_outbound_vumi_message_custom_config(
    submit_sm_processor_custom_config: SubmitShortMessageProcessor,
):
    """
    The custom config should be reflected in the generated PDU
    """
    processor = submit_sm_processor_custom_config
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content='Knights who say "Ni!"',
    )
    [pdu] = await processor.handle_outbound_message(message)
    assert pdu.params["service_type"] == b"test service type"
    assert pdu.params["source_addr_ton"] == AddrTon.SUBSCRIBER_NUMBER
    assert pdu.params["source_addr_npi"] == AddrNpi.NATIONAL
    assert pdu.params["source_addr"] == b"12345"
    assert pdu.params["dest_addr_ton"] == AddrTon.INTERNATIONAL
    assert pdu.params["dest_addr_npi"] == AddrNpi.TELEX
    assert pdu.params["destination_addr"] == b"+27820001001"
    assert pdu.params["registered_delivery"] == RegisteredDelivery(
        RegisteredDeliveryReceipt.SMSC_DELIVERY_RECEIPT_REQUESTED,
        [RegisteredDeliverySmeOriginatedAcks.SME_MANUAL_ACK_REQUESTED],
        True,
    )
    assert pdu.params["data_coding"] == DataCoding(
        schemeData=DataCodingDefault.IA5_ASCII
    )
    assert pdu.params["short_message"] == b'Knights who say "Ni!"'
