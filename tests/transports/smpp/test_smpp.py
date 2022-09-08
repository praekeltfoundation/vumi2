import logging

from pytest import fixture
from smpp.pdu.operations import BindTransceiverResp, SubmitSMResp
from trio import Nursery, open_nursery

from vumi2.connectors import ReceiveOutboundConnector
from vumi2.messages import Message, TransportType
from vumi2.transports.smpp.client import EsmeClient
from vumi2.transports.smpp.smpp import (
    SmppTransceiverTransport,
    SmppTransceiverTransportConfig,
)

from .helpers import TcpFakeSmsc


@fixture
async def tcp_smsc(nursery):
    """
    Creates a TCP-based FakeSmsc server listening on an arbitrary port.
    """
    server = TcpFakeSmsc(nursery)
    await server.serve_tcp()
    return server


@fixture
async def transport(nursery, amqp_connection, tcp_smsc):
    """
    An SMPP transciever transport, with default config except connecting to the
    port that tcp_smsc is listening on.
    """
    config = SmppTransceiverTransportConfig(port=tcp_smsc.port)
    return SmppTransceiverTransport(nursery, amqp_connection, config)


async def test_startup(transport, tcp_smsc, nursery: Nursery):
    """
    For the transport's `setup`, it should create and bind/start a client, and an AMQP
    connector.
    """
    async with open_nursery() as start_nursery:
        start_nursery.start_soon(transport.setup)
        # Receive the bind request and respond, so that setup can complete
        bind_pdu = await tcp_smsc.receive_pdu()
        await tcp_smsc.send_pdu(BindTransceiverResp(seqNum=bind_pdu.seqNum))

    assert isinstance(transport.connector, ReceiveOutboundConnector)
    assert isinstance(transport.client, EsmeClient)


async def test_outbound_message(
    transport: SmppTransceiverTransport, tcp_smsc: TcpFakeSmsc, nursery: Nursery
):
    """
    Outbound messages should send a submit short message PDU to the server
    """
    async with open_nursery() as start_nursery:
        start_nursery.start_soon(transport.setup)
        await tcp_smsc.handle_bind()

    message = Message(
        from_addr="123456",
        to_addr="+27820001001",
        transport_name="sms",
        transport_type=TransportType.SMS,
        content="test",
    )

    async with open_nursery() as send_message_nursery:
        send_message_nursery.start_soon(transport.handle_outbound, message)
        pdu = await tcp_smsc.receive_pdu()
        await tcp_smsc.send_pdu(SubmitSMResp(seqNum=pdu.seqNum))

    assert pdu.params["short_message"] == b"test"


async def test_handle_inbound_message_or_event_invalid(
    transport: SmppTransceiverTransport, tcp_smsc: TcpFakeSmsc, caplog
):
    """
    If we receive an invalid type, it should be logged at an error level.
    """
    async with open_nursery() as start_nursery:
        start_nursery.start_soon(transport.setup)
        await tcp_smsc.handle_bind()
        await transport.client.send_message_channel.send(object())

    [log] = [log for log in caplog.records if log.levelno >= logging.ERROR]

    assert "Received invalid message type" in log.getMessage()
