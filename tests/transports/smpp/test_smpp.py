from io import BytesIO

from pytest import fixture
from smpp.pdu.operations import BindTransceiverResp
from smpp.pdu.pdu_encoding import PDUEncoder
from trio import Nursery, open_memory_channel, open_nursery, serve_tcp

from vumi2.connectors import ReceiveOutboundConnector
from vumi2.transports.smpp.client import EsmeClient
from vumi2.transports.smpp.smpp import (
    SmppTransceiverTransport,
    SmppTransceiverTransportConfig,
)


class TcpServer:
    """
    A TCP server, that uses trio in memory channels to send and receive data
    """

    def __init__(self, nursery):
        self.nursery = nursery
        self._from_stream_send, self._from_stream_receive = open_memory_channel(0)
        self._to_stream_send, self._to_stream_receive = open_memory_channel(0)

    async def producer(self, stream):
        async for data in stream:
            await self._from_stream_send.send(data)

    async def server(self, stream):
        self.nursery.start_soon(self.producer, stream)
        async for data in self._to_stream_receive:
            await stream.send_all(data)

    @property
    def send_channel(self):
        return self._to_stream_send

    @property
    def receive_channel(self):
        return self._from_stream_receive

    async def run(self):
        listeners = await self.nursery.start(serve_tcp, self.server, 0)
        self.port = listeners[0].socket.getsockname()[1]


@fixture
async def tcp_server(nursery):
    """
    Creates a TCP server listening on port TEST_PORT, and returns a send and receive
    trio in memory channel.

    Used to convert a TCP stream to an in memory channel stream, to provide a place for
    the transport to connect to, and to be able to simulate an SMPP server in the tests
    """
    server = TcpServer(nursery)
    await server.run()
    return server


@fixture
async def transport(nursery, amqp_connection, tcp_server):
    """
    An SMPP transciever transport, with default config except connecting to the
    TEST_PORT
    """
    config = SmppTransceiverTransportConfig(port=tcp_server.port)
    return SmppTransceiverTransport(nursery, amqp_connection, config)


async def test_startup(transport, tcp_server, nursery: Nursery):
    """
    For the transport's `setup`, it should create and bind/start a client, and an AMQP
    connector.
    """
    async with open_nursery() as start_nursery:
        start_nursery.start_soon(transport.setup)
        # Receive the bind request and respond, so that setup can complete
        pdu_data = await tcp_server.receive_channel.receive()
        pdu = PDUEncoder().decode(BytesIO(pdu_data))
        response_pdu = BindTransceiverResp(seqNum=pdu.seqNum)
        await tcp_server.send_channel.send(PDUEncoder().encode(response_pdu))

    assert isinstance(transport.connector, ReceiveOutboundConnector)
    assert isinstance(transport.client, EsmeClient)
