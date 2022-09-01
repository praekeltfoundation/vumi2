from io import BytesIO

from pytest import fixture
from smpp.pdu.operations import BindTransceiverResp
from smpp.pdu.pdu_encoding import PDUEncoder
from trio import Nursery, open_nursery

from vumi2.connectors import ReceiveOutboundConnector
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
