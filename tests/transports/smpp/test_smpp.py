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

TEST_PORT = 8000


@fixture
async def tcp_server(nursery):
    from_stream_send, from_stream_receive = open_memory_channel(0)
    to_stream_send, to_stream_receive = open_memory_channel(0)

    async def producer(stream):
        async for data in stream:
            await from_stream_send.send(data)

    async def server(stream):
        nursery.start_soon(producer, stream)
        async for data in to_stream_receive:
            await stream.send_all(data)

    nursery.start_soon(serve_tcp, server, TEST_PORT)
    return to_stream_send, from_stream_receive


@fixture
async def transport(nursery, amqp_connection):
    config = SmppTransceiverTransportConfig(port=TEST_PORT)
    return SmppTransceiverTransport(nursery, amqp_connection, config)


async def test_startup(transport, tcp_server, nursery: Nursery):
    send_channel, receive_channel = tcp_server
    async with open_nursery() as start_nursery:
        start_nursery.start_soon(transport.setup)
        # Receive the bind request and respond, so that setup can complete
        pdu_data = await receive_channel.receive()
        pdu = PDUEncoder().decode(BytesIO(pdu_data))
        response_pdu = BindTransceiverResp(seqNum=pdu.seqNum)
        await send_channel.send(PDUEncoder().encode(response_pdu))

    assert isinstance(transport.connector, ReceiveOutboundConnector)
    assert isinstance(transport.client, EsmeClient)
