import logging
from io import BytesIO

from pytest import fixture, raises
from smpp.pdu.operations import (
    BindTransceiver,
    BindTransceiverResp,
    EnquireLink,
    EnquireLinkResp,
    Outbind,
    SubmitSM,
    SubmitSMResp,
)
from smpp.pdu.pdu_encoding import PDUEncoder
from smpp.pdu.pdu_types import CommandStatus
from trio.testing import memory_stream_pair

from vumi2.transports.smpp.client import EsmeClient, EsmeResponseStatusError
from vumi2.transports.smpp.smpp import SmppTransceiverTransportConfig


def test_extract_pdu():
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


@fixture
async def stream():
    client, server = memory_stream_pair()
    return (client, server)


@fixture
async def client_stream(stream):
    return stream[0]


@fixture
async def server_stream(stream):
    return stream[1]


@fixture
async def client(nursery, client_stream) -> EsmeClient:
    config = SmppTransceiverTransportConfig()
    return EsmeClient(nursery, client_stream, config)


async def test_get_next_sequence_value(client: EsmeClient):
    """The allowed sequence_number range is from 0x00000001 to 0x7FFFFFFF"""
    client.sequence_number = 0
    assert await client.get_next_sequence_number() == 1
    assert await client.get_next_sequence_number() == 2
    assert await client.get_next_sequence_number() == 3
    # wrap around at 0xFFFFFFFF
    client.sequence_number = 0x7FFFFFFE
    assert await client.get_next_sequence_number() == 0x7FFFFFFF
    assert await client.get_next_sequence_number() == 1


async def test_start(client: EsmeClient, server_stream, nursery):
    """Client should try to bind, and on binding, should start to send enquires"""
    nursery.start_soon(client.start)

    pdu_data = await server_stream.receive_some()
    pdu = PDUEncoder().decode(BytesIO(pdu_data))
    assert isinstance(pdu, BindTransceiver)
    assert pdu.params["system_id"] == b"smppclient1"
    assert pdu.params["password"] == b"password"
    assert pdu.params["interface_version"] == 34

    response_pdu = BindTransceiverResp(seqNum=pdu.seqNum)
    response = PDUEncoder().encode(response_pdu)
    await server_stream.send_all(response)

    enquire_data = await server_stream.receive_some()
    enquire_pdu = PDUEncoder().decode(BytesIO(enquire_data))
    assert isinstance(enquire_pdu, EnquireLink)

    enquire_response_pdu = EnquireLinkResp(seqNum=enquire_pdu.seqNum)
    enquire_response = PDUEncoder().encode(enquire_response_pdu)
    await server_stream.send_all(enquire_response)


async def test_send_response_pdu(client: EsmeClient, server_stream):
    """Response PDUs should not wait for a reply"""
    pdu = SubmitSMResp(seqNum=1, message_id="test")
    await client.send_pdu(pdu)

    received_data = await server_stream.receive_some()
    received_pdu = PDUEncoder().decode(BytesIO(received_data))

    assert received_pdu == pdu


async def test_send_pdu_error_response(client: EsmeClient, server_stream):
    """If we don't get an ESME_ROK back, raise an error"""
    pdu = EnquireLink(seqNum=1)
    task = client.send_pdu(pdu)

    response_pdu = EnquireLinkResp(
        seqNum=pdu.seqNum, status=CommandStatus.ESME_RX_P_APPN
    )
    await server_stream.send_all(PDUEncoder().encode(response_pdu))
    with raises(EsmeResponseStatusError):
        await task


async def test_send_pdu_wrong_response(client: EsmeClient, server_stream):
    """
    If we receive a PDU with the correct sequence number, but incorrect response type,
    we should raise an error
    """
    pdu = EnquireLink(seqNum=1)
    task = client.send_pdu(pdu)

    response_pdu = BindTransceiverResp(seqNum=pdu.seqNum)
    await server_stream.send_all(PDUEncoder().encode(response_pdu))
    with raises(EsmeResponseStatusError):
        await task


async def test_handle_pdu_invalid_type(client: EsmeClient, caplog):
    """Only data requests and responses should be sent by the server"""
    pdu = Outbind(seqNum=1)
    await client.handle_pdu(pdu)
    [log] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert "Unknown PDU type" in log.getMessage()


async def test_handle_pdu_unknown_command(client: EsmeClient, caplog):
    """We should log an error if we receive a data request that we don't yet handle"""
    pdu = SubmitSM(seqNum=1)
    await client.handle_pdu(pdu)
    [log] = [log for log in caplog.records if log.levelno >= logging.WARNING]
    assert "Received PDU with unknown command name" in log.getMessage()


async def test_handle_known_command(client: EsmeClient):
    """Should call the handler function"""
    received_pdu = None

    async def handler(pdu):
        nonlocal received_pdu
        received_pdu = pdu

    setattr(client, "handle_submit_sm", handler)
    pdu = SubmitSM(seqNum=1)
    await client.handle_pdu(pdu)
    assert received_pdu == pdu
