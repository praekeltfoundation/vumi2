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
from vumi2.transports.smpp.sequencers import InMemorySequencer
from vumi2.transports.smpp.smpp import SmppTransceiverTransportConfig


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


@fixture
async def stream():
    """
    A trio memory stream pair. Done as a fixture so that the same stream can easily
    injected into other fixtures and the test method, without ending up with duplicates
    that aren't connected.
    """
    client, server = memory_stream_pair()
    return (client, server)


@fixture
async def client_stream(stream):
    """The client part of the trio stream"""
    return stream[0]


@fixture
async def server_stream(stream):
    """The server part of the trio stream"""
    return stream[1]


@fixture
async def client(nursery, client_stream) -> EsmeClient:
    """An EsmeClient with default config"""
    config = SmppTransceiverTransportConfig()
    return EsmeClient(nursery, client_stream, config, InMemorySequencer({}))


async def complete_client_startup(client, server_stream):
    """
    Receives and responds to the client's bind request, and its first enquire link
    request, completing the startup of the client and making it ready to accept commands
    """
    client.nursery.start_soon(client.start)
    pdu_data = await server_stream.receive_some()
    pdu = PDUEncoder().decode(BytesIO(pdu_data))

    response_pdu = BindTransceiverResp(seqNum=pdu.seqNum)
    response = PDUEncoder().encode(response_pdu)
    await server_stream.send_all(response)

    enquire_data = await server_stream.receive_some()
    enquire_pdu = PDUEncoder().decode(BytesIO(enquire_data))

    enquire_response_pdu = EnquireLinkResp(seqNum=enquire_pdu.seqNum)
    enquire_response = PDUEncoder().encode(enquire_response_pdu)
    await server_stream.send_all(enquire_response)


async def test_start(client: EsmeClient, server_stream):
    """Client should try to bind, and on binding, should start to send enquires"""
    client.nursery.start_soon(client.start)
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
    await complete_client_startup(client, server_stream)

    pdu = SubmitSMResp(seqNum=1, message_id="test")
    await client.send_pdu(pdu)

    received_data = await server_stream.receive_some()
    received_pdu = PDUEncoder().decode(BytesIO(received_data))

    assert received_pdu == pdu


async def test_send_pdu_error_response(client: EsmeClient, server_stream):
    """If we don't get an ESME_ROK back, raise an error"""
    await complete_client_startup(client, server_stream)

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
    await complete_client_startup(client, server_stream)

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
