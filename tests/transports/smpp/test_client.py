import logging

import pytest
from smpp.pdu.operations import (
    BindTransceiver,
    BindTransceiverResp,
    DeliverSM,
    DeliverSMResp,
    EnquireLink,
    EnquireLinkResp,
    GenericNack,
    Outbind,
    SubmitSM,
    SubmitSMResp,
    Unbind,
    UnbindResp,
)
from smpp.pdu.pdu_types import (
    CommandStatus,
    DataCoding,
    EsmClass,
    EsmClassMode,
    EsmClassType,
)
from trio import open_memory_channel
from trio.testing import memory_stream_pair

from vumi2.messages import DeliveryStatus, Event, EventType, Message, TransportType
from vumi2.transports.smpp.client import (
    BindTimeout,
    EnquireLinkTimeout,
    EsmeClient,
    EsmeResponseStatusError,
    SmscUnbind,
)
from vumi2.transports.smpp.processors import (
    DeliveryReportProcesser,
    ShortMessageProcessor,
    SubmitShortMessageProcessor,
)
from vumi2.transports.smpp.sequencers import InMemorySequencer
from vumi2.transports.smpp.smpp import SmppTransceiverTransportConfig
from vumi2.transports.smpp.smpp_cache import InMemorySmppCache

from .helpers import FakeSmsc, open_autocancel_nursery


def test_extract_pdu():
    """
    If the whole data is too short for an SMPP packet, or if the data is shorter than
    the specified packet length, should return None and leave data alone.

    If the data is equal to or bigger than the specified packet length, then it should
    return that packet, and remove it from data
    """
    # Too short
    assert EsmeClient.extract_pdu(bytearray.fromhex("00000004")) is None
    # Not complete packet
    assert (
        EsmeClient.extract_pdu(bytearray.fromhex("00000020 050607080910111213141516"))
        is None
    )
    # Complete packet
    complete_packet = bytearray.fromhex("00000010 050607080910111213141516")
    assert EsmeClient.extract_pdu(complete_packet) == bytearray.fromhex(
        "00000010 050607080910111213141516"
    )
    assert complete_packet == b""
    # More than one packet
    packet_with_extra = bytearray.fromhex("00000010 050607080910111213141516 010203")
    assert EsmeClient.extract_pdu(packet_with_extra) == bytearray.fromhex(
        "00000010 050607080910111213141516"
    )
    assert packet_with_extra == bytearray.fromhex("010203")


@pytest.fixture
async def stream():
    """
    A trio memory stream pair. Done as a fixture so that the same stream can easily
    injected into other fixtures and the test method, without ending up with duplicates
    that aren't connected.
    """
    client, server = memory_stream_pair()
    return (client, server)


@pytest.fixture
async def client_stream(stream):
    """The client part of the trio stream"""
    return stream[0]


@pytest.fixture
async def server_stream(stream):
    """The server part of the trio stream"""
    return stream[1]


@pytest.fixture
async def sequencer():
    return InMemorySequencer({})


@pytest.fixture
async def smpp_cache():
    return InMemorySmppCache({})


@pytest.fixture
async def submit_sm_processor(sequencer):
    return SubmitShortMessageProcessor({}, sequencer)


@pytest.fixture
async def sm_processor(smpp_cache):
    return ShortMessageProcessor({}, smpp_cache)


@pytest.fixture
async def dr_processor(smpp_cache):
    return DeliveryReportProcesser({}, smpp_cache)


@pytest.fixture
async def message_channel():
    return open_memory_channel(1)


@pytest.fixture
async def send_message_channel(message_channel):
    return message_channel[0]


@pytest.fixture
async def receive_message_channel(message_channel):
    return message_channel[1]


@pytest.fixture
async def client(
    nursery,
    client_stream,
    sequencer,
    smpp_cache,
    submit_sm_processor,
    sm_processor,
    dr_processor,
    send_message_channel,
) -> EsmeClient:
    """An EsmeClient with default config"""
    config = SmppTransceiverTransportConfig()
    return EsmeClient(
        nursery,
        client_stream,
        config,
        sequencer,
        smpp_cache,
        submit_sm_processor,
        sm_processor,
        dr_processor,
        send_message_channel,
    )


@pytest.fixture
async def smsc(server_stream) -> FakeSmsc:
    """A FakeSmsc"""
    return FakeSmsc(server_stream)


async def test_start(client: EsmeClient, smsc: FakeSmsc):
    """Client should try to bind, and on binding, should start to send enquires"""
    client.nursery.start_soon(client.start)

    bind_pdu = await smsc.receive_pdu()
    assert isinstance(bind_pdu, BindTransceiver)
    assert bind_pdu.params["system_id"] == b"smppclient1"
    assert bind_pdu.params["password"] == b"password"
    assert bind_pdu.params["interface_version"] == 34

    await smsc.send_pdu(BindTransceiverResp(seqNum=bind_pdu.seqNum))

    enquire_pdu = await smsc.receive_pdu()
    assert isinstance(enquire_pdu, EnquireLink)

    await smsc.send_pdu(EnquireLinkResp(seqNum=enquire_pdu.seqNum))


async def test_bind_timeout(client: EsmeClient, autojump_clock):
    """
    Client should try to bind, and raise an exception if the bind response took too long
    """
    with pytest.raises(BindTimeout):
        await client.start()


async def test_enquire_link_timeout(client: EsmeClient, smsc: FakeSmsc, autojump_clock):
    """
    Client should try to bind, and raise an exception if the bind response took too long
    """
    await smsc.start_and_bind(client)

    with pytest.raises(EnquireLinkTimeout):
        await client.enquire_link()


async def test_send_response_pdu(client: EsmeClient, smsc: FakeSmsc):
    """Response PDUs should not wait for a reply"""
    await smsc.start_and_bind(client)

    pdu = SubmitSMResp(seqNum=1, message_id="test")
    await client.send_pdu(pdu)

    received_pdu = await smsc.receive_pdu()

    assert received_pdu == pdu


async def test_send_pdu_error_response(client: EsmeClient, smsc: FakeSmsc):
    """If we don't get an ESME_ROK back, raise an error"""
    await smsc.start_and_bind(client)

    pdu = EnquireLink(seqNum=1)
    task = client.send_pdu(pdu)

    response_pdu = EnquireLinkResp(
        seqNum=pdu.seqNum, status=CommandStatus.ESME_RX_P_APPN
    )
    await smsc.send_pdu(response_pdu)
    with pytest.raises(EsmeResponseStatusError):
        await task


async def test_send_pdu_wrong_response(client: EsmeClient, smsc: FakeSmsc):
    """
    If we receive a PDU with the correct sequence number, but incorrect response type,
    we should raise an error
    """
    await smsc.start_and_bind(client)

    pdu = EnquireLink(seqNum=1)
    task = client.send_pdu(pdu)

    await smsc.send_pdu(BindTransceiverResp(seqNum=pdu.seqNum))
    with pytest.raises(EsmeResponseStatusError):
        await task


async def test_handle_pdu_invalid_type(client: EsmeClient, caplog):
    """Only data requests and responses should be sent by the server"""
    pdu = Outbind(seqNum=1)
    await client.handle_pdu(pdu)
    [log] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert "Unknown PDU type" in log.getMessage()


async def test_handle_pdu_unknown_command(client: EsmeClient, caplog, smsc):
    """
    We should log an error and respond with a generic nack if we receive a request
    that we don't handle
    """
    await smsc.start_and_bind(client)

    pdu = SubmitSM(seqNum=1)
    await client.handle_pdu(pdu)

    [log] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert "Received PDU with unknown command name" in log.getMessage()

    nack = await smsc.receive_pdu()
    assert isinstance(nack, GenericNack)
    assert nack.status == CommandStatus.ESME_RINVCMDID


async def test_handle_known_command(client: EsmeClient):
    """Should call the handler function"""
    received_pdu = None

    async def handler(pdu):
        nonlocal received_pdu
        received_pdu = pdu

    setattr(client, "handle_submit_sm", handler)  # noqa: B010 (For mypy.)
    pdu = SubmitSM(seqNum=1)
    await client.handle_pdu(pdu)
    assert received_pdu == pdu


async def test_send_vumi_message(client: EsmeClient, smsc: FakeSmsc):
    """Sends the PDU/s that represent the vumi message"""
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content='Knights who say "Nì!"',
    )

    client.nursery.start_soon(client.send_vumi_message, message)

    pdu = await smsc.receive_pdu()
    assert pdu.params["source_addr"] == b"12345"
    assert pdu.params["destination_addr"] == b"+27820001001"
    assert pdu.params["short_message"] == b'Knights who say "N\x07!"'

    response_pdu = SubmitSMResp(seqNum=pdu.seqNum)
    await smsc.send_pdu(response_pdu)


async def test_submit_sm_resp_ack(client: EsmeClient, receive_message_channel, smsc):
    """
    If the response PDU is ESME_ROK, send an ack
    """
    await smsc.start_and_bind(client)

    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content='Knights who say "Nì!"',
    )

    client.nursery.start_soon(client.send_vumi_message, message)
    msg_pdu = await smsc.receive_pdu()

    pdu = SubmitSMResp(seqNum=msg_pdu.seqNum)
    await smsc.send_pdu(pdu)

    event = await receive_message_channel.receive()
    assert isinstance(event, Event)
    assert event.event_type == EventType.ACK


async def test_submit_sm_resp_nack(client: EsmeClient, receive_message_channel, smsc):
    """
    If the response PDU is not ESME_ROK, send a nack with reason
    """
    await smsc.start_and_bind(client)
    message = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content='Knights who say "Nì!"',
    )

    client.nursery.start_soon(client.send_vumi_message, message)
    msg_pdu = await smsc.receive_pdu()

    pdu = SubmitSMResp(seqNum=msg_pdu.seqNum, status=CommandStatus.ESME_RINVMSGLEN)
    await smsc.send_pdu(pdu)

    event = await receive_message_channel.receive()
    assert isinstance(event, Event)
    assert event.event_type == EventType.NACK
    assert event.nack_reason == "Message Length is invalid"


async def test_handle_deliver_sm(
    client: EsmeClient, smsc: FakeSmsc, receive_message_channel
):
    """
    DeliverSM PDU creates an inbound message
    """
    await smsc.start_and_bind(client)

    pdu = DeliverSM(
        short_message=b"test message",
        source_addr=b"27820001001",
        destination_addr=b"123456",
        data_coding=DataCoding(),
    )

    await smsc.send_pdu(pdu)

    msg = await receive_message_channel.receive()
    assert msg.content == "test message"
    assert msg.to_addr == "123456"
    assert msg.from_addr == "27820001001"

    resp = await smsc.receive_pdu()
    assert isinstance(resp, DeliverSMResp)
    assert resp.seqNum == pdu.seqNum


async def test_handle_deliver_sm_delivery_report(
    client: EsmeClient,
    smsc: FakeSmsc,
    receive_message_channel,
    smpp_cache: InMemorySmppCache,
):
    """
    DeliverSM PDU creates an event on delivery report
    """
    await smsc.start_and_bind(client)

    await smpp_cache.store_smpp_message_id("vumimsgid", "0123456789")
    pdu = DeliverSM(
        esm_class=EsmClass(EsmClassMode.DEFAULT, EsmClassType.DEFAULT),
        short_message=(
            b"id:0123456789 sub:001 dlvrd:001 submit date:2209121354 done"
            b" date:2209121454 stat:REJECTD Text:01234567890123456789"
        ),
    )

    await smsc.send_pdu(pdu)

    event = await receive_message_channel.receive()
    assert event.user_message_id == "vumimsgid"
    assert event.event_type == EventType.DELIVERY_REPORT
    assert event.delivery_status == DeliveryStatus.FAILED
    assert event.transport_metadata == {"smpp_delivery_status": "REJECTD"}

    resp = await smsc.receive_pdu()
    assert isinstance(resp, DeliverSMResp)
    assert resp.seqNum == pdu.seqNum


async def test_handle_unbind(client: EsmeClient, smsc: FakeSmsc):
    """
    Unbind should respond, then raise an exception
    """
    with pytest.raises(SmscUnbind):
        async with open_autocancel_nursery() as nursery:
            client.nursery = nursery
            await smsc.start_and_bind(client)

            pdu = Unbind(seqNum=1)
            await smsc.send_pdu(pdu)

            resp = await smsc.receive_pdu()
            assert isinstance(resp, UnbindResp)
            assert resp.seqNum == pdu.seqNum


async def test_handle_enquire_link(client: EsmeClient, smsc: FakeSmsc):
    """
    We should send enquire link responses to enquire links
    """
    await smsc.start_and_bind(client)

    pdu = EnquireLink(seqNum=1)
    await smsc.send_pdu(pdu)

    resp = await smsc.receive_pdu()
    assert isinstance(resp, EnquireLinkResp)
    assert resp.seqNum == pdu.seqNum
