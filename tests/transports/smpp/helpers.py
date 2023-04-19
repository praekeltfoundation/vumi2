from contextlib import asynccontextmanager
from io import BytesIO

from smpp.pdu.operations import BindTransceiverResp, EnquireLinkResp  # type: ignore
from smpp.pdu.pdu_encoding import PDUEncoder  # type: ignore
from smpp.pdu.pdu_types import PDU  # type: ignore
from trio import open_nursery, serve_tcp
from trio.testing import memory_stream_pair


class FakeSmsc:
    """
    A fake SMSC server suitable for testing EsmeClient.
    """

    def __init__(self, server_stream):
        self._stream = server_stream

    async def receive_pdu(self) -> PDU:
        """Receive and decode a PDU from the connected client."""
        pdu_data = await self._stream.receive_some()
        return PDUEncoder().decode(BytesIO(pdu_data))

    async def send_pdu(self, pdu: PDU) -> None:
        """Send a PDU to the connected client."""
        pdu_data = PDUEncoder().encode(pdu)
        await self._stream.send_all(pdu_data)

    async def handle_bind(self):
        """
        Receives and responds to the client's bind request, and its first enquire
        link request, completing the startup of the client and making it ready
        to accept commands
        """
        bind_pdu = await self.receive_pdu()
        await self.send_pdu(BindTransceiverResp(seqNum=bind_pdu.seqNum))
        enquire_pdu = await self.receive_pdu()
        await self.send_pdu(EnquireLinkResp(seqNum=enquire_pdu.seqNum))

    async def start_and_bind(self, client):
        """Start the given client and handle startup."""
        client.nursery.start_soon(client.start)
        await self.handle_bind()


class TcpFakeSmsc(FakeSmsc):
    """
    A fake SMSC server that communicates over TCP.
    """

    def __init__(self, nursery):
        self.nursery = nursery
        self._client_stream, server_stream = memory_stream_pair()
        super().__init__(server_stream)

    async def producer(self, stream):
        async for data in stream:
            await self._client_stream.send_all(data)

    async def server(self, stream):
        self.nursery.start_soon(self.producer, stream)
        async for data in self._client_stream:
            await stream.send_all(data)

    async def serve_tcp(self):
        listeners = await self.nursery.start(serve_tcp, self.server, 0)
        self.port = listeners[0].socket.getsockname()[1]


@asynccontextmanager
async def open_autocancel_nursery():
    async with open_nursery() as nursery:
        try:
            yield nursery
        finally:
            nursery.cancel_scope.cancel()
