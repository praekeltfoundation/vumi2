from io import BytesIO
# from typing import Optional

from smpp.pdu.operations import BindTransceiverResp, EnquireLinkResp
from smpp.pdu.pdu_encoding import PDUEncoder
from smpp.pdu.pdu_types import PDU


class FakeSmsc:
    """
    A fake SMSC server suitable for testing EsmeClient.
    """

    def __init__(self, server_stream):
        self.stream = server_stream

    async def receive_pdu(self) -> PDU:
        """Receive and decode a PDU from the connected client."""
        pdu_data = await self.stream.receive_some()
        return PDUEncoder().decode(BytesIO(pdu_data))

    async def send_pdu(self, pdu: PDU) -> None:
        """Send a PDU to the connected client."""
        pdu_data = PDUEncoder().encode(pdu)
        await self.stream.send_all(pdu_data)

    async def start_and_bind(self, client):
        """
        Start the given client and handle startup.

        Receives and responds to the client's bind request, and its first enquire
        link request, completing the startup of the client and making it ready
        to accept commands
        """
        client.nursery.start_soon(client.start)
        bind_pdu = await self.receive_pdu()
        await self.send_pdu(BindTransceiverResp(seqNum=bind_pdu.seqNum))
        enquire_pdu = await self.receive_pdu()
        await self.send_pdu(EnquireLinkResp(seqNum=enquire_pdu.seqNum))
