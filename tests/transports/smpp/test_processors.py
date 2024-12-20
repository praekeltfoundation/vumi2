import logging

import pytest
from cattrs.errors import ClassValidationError
from smpp.pdu.operations import DeliverSM  # type: ignore
from smpp.pdu.pdu_types import (  # type: ignore
    AddrNpi,
    AddrTon,
    DataCoding,
    DataCodingDefault,
    EsmClass,
    EsmClassMode,
    EsmClassType,
    MessageState,
    RegisteredDelivery,
    RegisteredDeliveryReceipt,
    RegisteredDeliverySmeOriginatedAcks,
)

from vumi2.messages import DeliveryStatus, EventType, Message, TransportType
from vumi2.transports.smpp.processors import (
    DeliveryReportProcesser,
    MultipartHandling,
    ShortMessageProcessor,
    SubmitShortMessageProcessor,
)
from vumi2.transports.smpp.sequencers import InMemorySequencer
from vumi2.transports.smpp.smpp_cache import InMemorySmppCache


@pytest.fixture()
async def sequencer() -> InMemorySequencer:
    return InMemorySequencer({})


@pytest.fixture()
async def smpp_cache() -> InMemorySmppCache:
    return InMemorySmppCache({})


@pytest.fixture()
async def submit_sm_processor(
    sequencer: InMemorySequencer,
) -> SubmitShortMessageProcessor:
    return SubmitShortMessageProcessor({}, sequencer)


@pytest.fixture()
async def dr_processer(smpp_cache) -> DeliveryReportProcesser:
    return DeliveryReportProcesser({}, smpp_cache)


@pytest.fixture()
async def sm_processer(smpp_cache: InMemorySmppCache) -> ShortMessageProcessor:
    return ShortMessageProcessor(
        {"data_coding_overrides": {"OCTET_UNSPECIFIED": "ascii"}}, smpp_cache
    )


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


async def test_submit_sm_outbound_blank_vumi_message(
    submit_sm_processor: SubmitShortMessageProcessor,
):
    """
    Empty message bodies should result in an empty string short_message
    """
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content=None,
    )
    [pdu] = await submit_sm_processor.handle_outbound_message(message)
    assert pdu.params["source_addr"] == b"12345"
    assert pdu.params["destination_addr"] == b"+27820001001"
    assert pdu.params["short_message"] == b""


@pytest.fixture()
async def submit_sm_processor_custom_config(
    sequencer: InMemorySequencer,
) -> SubmitShortMessageProcessor:
    cfg = {
        "data_coding": "IA5_ASCII",
        "multipart_handling": "message_payload",
        "service_type": "test service type",
        "source_addr_ton": "SUBSCRIBER_NUMBER",
        "source_addr_npi": "NATIONAL",
        "dest_addr_ton": "INTERNATIONAL",
        "dest_addr_npi": "TELEX",
        "registered_delivery": {
            "delivery_receipt": "SMSC_DELIVERY_RECEIPT_REQUESTED",
            "sme_originated_acks": ["SME_MANUAL_ACK_REQUESTED"],
            "intermediate_notification": True,
        },
    }
    return SubmitShortMessageProcessor(cfg, sequencer)


async def test_config_conversion_ints(sequencer: InMemorySequencer):
    """
    Enum config values may not be specified as integers, because the
    enum values don't match the SMPP spec and are effectively arbitrary.
    """
    cfg_dict = {
        "data_coding": 1,
        "multipart_handling": "message_payload",
        "service_type": "test service type",
        "source_addr_ton": 4,
        "source_addr_npi": 5,
        "dest_addr_ton": 1,
        "dest_addr_npi": 3,
        "registered_delivery": {
            "delivery_receipt": 1,
            "sme_originated_acks": [1],
            "intermediate_notification": True,
        },
    }

    with pytest.raises(ClassValidationError) as e_info:
        SubmitShortMessageProcessor(cfg_dict, sequencer)

    # We get nested ClassValidationErrors until we reach the actual exception
    # we need to assert on.
    err: Exception = e_info.value
    while isinstance(err, ClassValidationError):
        [err] = err.exceptions
    assert str(err) == "Enums must be specified by name"


async def test_config_conversion_strs(sequencer: InMemorySequencer):
    """
    Various enum config values may be specified as strings.
    """
    cfg_dict = {
        "data_coding": "IA5_ASCII",
        "multipart_handling": "message_payload",
        "service_type": "test service type",
        "source_addr_ton": "SUBSCRIBER_NUMBER",
        "source_addr_npi": "NATIONAL",
        "dest_addr_ton": "INTERNATIONAL",
        "dest_addr_npi": "TELEX",
        "registered_delivery": {
            "delivery_receipt": "SMSC_DELIVERY_RECEIPT_REQUESTED",
            "sme_originated_acks": ["SME_MANUAL_ACK_REQUESTED"],
            "intermediate_notification": True,
        },
    }
    cfg = SubmitShortMessageProcessor(cfg_dict, sequencer).config

    assert cfg.data_coding == DataCodingDefault.IA5_ASCII
    assert cfg.source_addr_ton == AddrTon.SUBSCRIBER_NUMBER
    assert cfg.source_addr_npi == AddrNpi.NATIONAL
    assert cfg.dest_addr_ton == AddrTon.INTERNATIONAL
    assert cfg.dest_addr_npi == AddrNpi.TELEX
    assert cfg.registered_delivery.delivery_receipt == (
        RegisteredDeliveryReceipt.SMSC_DELIVERY_RECEIPT_REQUESTED
    )
    assert cfg.registered_delivery.sme_originated_acks == [
        RegisteredDeliverySmeOriginatedAcks.SME_MANUAL_ACK_REQUESTED
    ]


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


async def test_submit_sm_outbound_vumi_message_message_payload(
    submit_sm_processor: SubmitShortMessageProcessor,
):
    """
    Creates a valid PDU representing the outbound vumi message, storing the contents of
    long messages in the message_payload portion
    """
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content='Knights who say "Nì!"' * 10,
    )
    submit_sm_processor.config.multipart_handling = MultipartHandling.message_payload
    [pdu] = await submit_sm_processor.handle_outbound_message(message)
    assert pdu.params["source_addr"] == b"12345"
    assert pdu.params["destination_addr"] == b"+27820001001"
    assert pdu.params["short_message"] is None
    assert pdu.params["message_payload"] == b'Knights who say "N\x07!"' * 10


async def test_submit_sm_outbound_vumi_message_csm_sar(
    submit_sm_processor: SubmitShortMessageProcessor,
):
    """
    Creates a valid PDU representing the outbound vumi message, storing the contents of
    long messages into multiple PDUs with sar parameters
    """
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content="1234567890" * 20,
    )
    submit_sm_processor.config.multipart_handling = MultipartHandling.multipart_sar
    [pdu1, pdu2] = await submit_sm_processor.handle_outbound_message(message)

    assert pdu1.params["source_addr"] == b"12345"
    assert pdu1.params["destination_addr"] == b"+27820001001"
    assert pdu1.params["short_message"] == b"1234567890" * 15 + b"123"
    assert pdu1.params["sar_msg_ref_num"] == 1
    assert pdu1.params["sar_total_segments"] == 2
    assert pdu1.params["sar_segment_seqnum"] == 1

    assert pdu2.params["source_addr"] == b"12345"
    assert pdu2.params["destination_addr"] == b"+27820001001"
    assert pdu2.params["short_message"] == b"4567890" + b"1234567890" * 4
    assert pdu2.params["sar_msg_ref_num"] == 1
    assert pdu2.params["sar_total_segments"] == 2
    assert pdu2.params["sar_segment_seqnum"] == 2


async def test_submit_sm_outbound_vumi_message_udh(
    submit_sm_processor: SubmitShortMessageProcessor,
):
    """
    Creates a valid PDU representing the outbound vumi message, storing the contents of
    long messages into multiple PDUs with UDH parameters as a header on the message
    content
    """
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content="1234567890" * 20,
    )
    submit_sm_processor.config.data_coding = DataCodingDefault.LATIN_1
    submit_sm_processor.config.multipart_handling = MultipartHandling.multipart_udh
    [pdu1, pdu2] = await submit_sm_processor.handle_outbound_message(message)

    assert pdu1.params["source_addr"] == b"12345"
    assert pdu1.params["destination_addr"] == b"+27820001001"
    assert (
        pdu1.params["short_message"]
        == b"\05\00\03\01\02\01" + b"1234567890" * 13 + b"1234"
    )

    assert pdu2.params["source_addr"] == b"12345"
    assert pdu2.params["destination_addr"] == b"+27820001001"
    assert (
        pdu2.params["short_message"]
        == b"\05\00\03\01\02\02" + b"567890" + b"1234567890" * 6
    )


async def test_submit_sm_outbound_vumi_message_fits_in_one(
    submit_sm_processor: SubmitShortMessageProcessor,
):
    """
    If a message is short enough to fit in a single message, then it shouldn't be split
    according to the multipart strategy
    """
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content="1234567890" * 16,
    )
    submit_sm_processor.config.data_coding = DataCodingDefault.IA5_ASCII
    submit_sm_processor.config.multipart_handling = MultipartHandling.multipart_udh
    [pdu] = await submit_sm_processor.handle_outbound_message(message)

    assert pdu.params["source_addr"] == b"12345"
    assert pdu.params["destination_addr"] == b"+27820001001"
    assert pdu.params["short_message"] == b"1234567890" * 16


async def test_submit_sm_msg_length(submit_sm_processor: SubmitShortMessageProcessor):
    """
    Should give the correct max length for different encoding types, and if this is a
    multipart message or not
    """
    # 7-bit codecs
    submit_sm_processor.config.data_coding = DataCodingDefault.SMSC_DEFAULT_ALPHABET
    assert submit_sm_processor._get_msg_length() == 160
    assert submit_sm_processor._get_msg_length(split_msg=True) == 153

    # 8-bit codecs
    submit_sm_processor.config.data_coding = DataCodingDefault.UCS2
    assert submit_sm_processor._get_msg_length() == 140
    assert submit_sm_processor._get_msg_length(split_msg=True) == 134


async def test_delivery_report_optional_params(
    dr_processer: DeliveryReportProcesser, smpp_cache: InMemorySmppCache
):
    """
    If there is a delivery report in the optional params, return an Event of it
    """
    await smpp_cache.store_smpp_message_id("messageid", "abc")
    handled, event = await dr_processer.handle_deliver_sm(
        DeliverSM(receipted_message_id="abc", message_state=MessageState.UNDELIVERABLE)
    )
    assert handled is True
    assert event is not None
    assert event.user_message_id == "messageid"
    assert event.event_type == EventType.DELIVERY_REPORT
    assert event.delivery_status == DeliveryStatus.FAILED
    assert event.transport_metadata == {"smpp_delivery_status": "UNDELIVERABLE"}


async def test_delivery_report_esm_class(
    dr_processer: DeliveryReportProcesser, smpp_cache
):
    """
    If the ESM class says this is a delivery report, return an Event of it
    """
    await smpp_cache.store_smpp_message_id("messageid", "0123456789")
    handled, event = await dr_processer.handle_deliver_sm(
        DeliverSM(
            esm_class=EsmClass(
                EsmClassMode.DEFAULT, EsmClassType.SMSC_DELIVERY_RECEIPT
            ),
            short_message=(
                b"id:0123456789 sub:001 dlvrd:001 submit date:2209121354 done"
                b" date:2209121454 stat:DELIVRD Text:01234567890123456789"
            ),
        )
    )
    assert handled is True
    assert event is not None
    assert event.user_message_id == "messageid"
    assert event.event_type == EventType.DELIVERY_REPORT
    assert event.delivery_status == DeliveryStatus.DELIVERED
    assert event.transport_metadata == {"smpp_delivery_status": "DELIVRD"}


async def test_delivery_report_body(dr_processer: DeliveryReportProcesser, smpp_cache):
    """
    If the body contains a delivery report, return an Event of it, even if the esm_class
    isn't one of a delivery report
    """
    await smpp_cache.store_smpp_message_id("messageid", "0123456789")
    handled, event = await dr_processer.handle_deliver_sm(
        DeliverSM(
            esm_class=EsmClass(EsmClassMode.DEFAULT, EsmClassType.DEFAULT),
            short_message=(
                b"id:0123456789 sub:001 dlvrd:001 submit date:2209121354 done"
                b" date:2209121454 stat:REJECTD Text:01234567890123456789"
            ),
        )
    )
    assert handled is True
    assert event is not None
    assert event.event_type == EventType.DELIVERY_REPORT
    assert event.delivery_status == DeliveryStatus.FAILED
    assert event.transport_metadata == {"smpp_delivery_status": "REJECTD"}


async def test_invalid_delivery_report_esm_class(
    dr_processer: DeliveryReportProcesser, caplog
):
    """
    If the ESM class says this is a delivery report, and we can't decode the body,
    then log a warning and don't return any event
    """
    handled, event = await dr_processer.handle_deliver_sm(
        DeliverSM(
            esm_class=EsmClass(
                EsmClassMode.DEFAULT, EsmClassType.SMSC_DELIVERY_RECEIPT
            ),
            short_message=b"not a delivery report",
        )
    )
    assert handled is False
    assert event is None
    [log] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert (
        log.getMessage()
        == "esm_class SMSC_DELIVERY_RECEIPT indicates delivery report, but regex"
        " does not match content: not a delivery report"
    )


async def test_delivery_report_none(dr_processer: DeliveryReportProcesser):
    """
    If it is not a delivery report, don't return any Event
    """
    handled, event = await dr_processer.handle_deliver_sm(
        DeliverSM(
            esm_class=EsmClass(EsmClassMode.DEFAULT, EsmClassType.DEFAULT),
            short_message=b"not a delivery report",
        )
    )
    assert handled is False
    assert event is None


async def test_delivery_report_no_cache(dr_processer: DeliveryReportProcesser, caplog):
    """
    If there isn't a message ID in the cache, then we cannot send an event, so log
    """
    handled, event = await dr_processer.handle_deliver_sm(
        DeliverSM(receipted_message_id="abc", message_state=MessageState.UNDELIVERABLE)
    )
    assert handled is True
    assert event is None
    [log] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert (
        log.getMessage()
        == "Unable to find message ID abc in SMPP cache, not sending status update"
        " UNDELIVERABLE"
    )


async def test_short_message(sm_processer: ShortMessageProcessor):
    """
    Normal short message should return the equivalent Message
    """
    message = await sm_processer.handle_deliver_sm(
        DeliverSM(
            short_message=b"test message",
            source_addr=b"27820001001",
            destination_addr=b"123456",
            data_coding=DataCoding(),
        )
    )
    assert message is not None
    assert message.content == "test message"
    assert message.from_addr == "27820001001"
    assert message.to_addr == "123456"
    assert message.transport_type == TransportType.SMS


async def test_short_message_codec_override(sm_processer: ShortMessageProcessor):
    """
    If the codec has been overwritten in the config, then we should use that
    """
    message = await sm_processer.handle_deliver_sm(
        DeliverSM(
            short_message=b"test message",
            source_addr=b"27820001001",
            destination_addr=b"123456",
            data_coding=DataCoding(schemeData=DataCodingDefault.OCTET_UNSPECIFIED),
        )
    )
    assert message is not None
    assert message.content == "test message"
    assert message.from_addr == "27820001001"
    assert message.to_addr == "123456"
    assert message.transport_type == TransportType.SMS


async def test_short_message_message_payload(sm_processer: ShortMessageProcessor):
    """
    We should still extract the message if it's in the message payload field
    """
    message = await sm_processer.handle_deliver_sm(
        DeliverSM(
            message_payload=b"test message",
            source_addr=b"27820001001",
            destination_addr=b"123456",
            data_coding=DataCoding(),
        )
    )
    assert message is not None
    assert message.content == "test message"
    assert message.from_addr == "27820001001"
    assert message.to_addr == "123456"
    assert message.transport_type == TransportType.SMS


async def test_short_message_extract_multipart(sm_processer: ShortMessageProcessor):
    """
    If it's a part of a multipart message, should return a tuple representing the
    extracted multipart parameters, otherwise return None
    """
    # Optional params
    pdu = DeliverSM(
        short_message=b"part1",
        sar_msg_ref_num=3,
        sar_total_segments=2,
        sar_segment_seqnum=1,
    )
    assert sm_processer._extract_multipart(pdu) == (3, 2, 1, b"part1")

    # No short_message or SAR params
    pdu = DeliverSM(short_message=None)
    assert sm_processer._extract_multipart(pdu) is None

    # CSM
    pdu = DeliverSM(short_message=b"\x05\x00\x03\x03\x02\x01part1")
    assert sm_processer._extract_multipart(pdu) == (3, 2, 1, b"part1")

    # CSM16
    pdu = DeliverSM(short_message=b"\x06\x08\x04\x01\x01\x02\x01part1")
    assert sm_processer._extract_multipart(pdu) == (257, 2, 1, b"part1")

    # Single part message
    pdu = DeliverSM(short_message=b"test message")
    assert sm_processer._extract_multipart(pdu) is None


async def test_short_message_multipart(sm_processer: ShortMessageProcessor):
    """
    Multipart messages should be combined and returned
    """
    pdu = DeliverSM(
        short_message=b"part1",
        sar_msg_ref_num=3,
        sar_total_segments=2,
        sar_segment_seqnum=1,
        source_addr=b"27820001001",
        destination_addr=b"123456",
        data_coding=DataCoding(),
    )
    msg = await sm_processer.handle_deliver_sm(pdu)
    assert msg is None

    pdu = DeliverSM(
        short_message=b"part2",
        sar_msg_ref_num=3,
        sar_total_segments=2,
        sar_segment_seqnum=2,
        source_addr=b"27820001001",
        destination_addr=b"123456",
        data_coding=DataCoding(),
    )
    msg = await sm_processer.handle_deliver_sm(pdu)
    assert msg is not None
    assert msg.content == "part1part2"
    assert msg.from_addr == "27820001001"
    assert msg.to_addr == "123456"


async def test_invalid_delivery_report_esm_class_bad_encoding(
    dr_processer: DeliveryReportProcesser, caplog
):
    """
    If the ESM class says this is a delivery report, and it isn't UTF8-compatible,
    then log a warning and don't return any event
    """
    handled, event = await dr_processer.handle_deliver_sm(
        DeliverSM(
            esm_class=EsmClass(
                EsmClassMode.DEFAULT, EsmClassType.SMSC_DELIVERY_RECEIPT
            ),
            short_message=b"\xa4\xe8 bad bytes",
        )
    )
    assert handled is False
    assert event is None
    [log] = [log for log in caplog.records if log.levelno >= logging.WARNING]

    assert (
        log.getMessage()
        == "esm_class SMSC_DELIVERY_RECEIPT indicates delivery report, but regex"
        " does not match content: ¤è bad bytes"
    )


async def test_delivery_report_body_bad_encoding(dr_processer: DeliveryReportProcesser):
    """
    If it is not valid UTF-8, don't throw a UnicodeDecodeError
    """
    handled, event = await dr_processer.handle_deliver_sm(
        DeliverSM(
            esm_class=EsmClass(EsmClassMode.DEFAULT, EsmClassType.DEFAULT),
            short_message=b"\xa4\xe8 bad bytes",
        )
    )
    assert handled is False
    assert event is None
