from io import BytesIO
from logging import getLogger
from typing import TYPE_CHECKING, Dict, Optional, Union, cast

from smpp.pdu.constants import command_status_name_map, command_status_value_map
from smpp.pdu.operations import (
    BindTransceiver,
    DeliverSM,
    DeliverSMResp,
    EnquireLink,
    GenericNack,
    PDURequest,
    PDUResponse,
)
from smpp.pdu.pdu_encoding import PDUEncoder
from smpp.pdu.pdu_types import PDU, AddrNpi, AddrTon, CommandStatus
from trio import (
    MemorySendChannel,
    Nursery,
    SocketStream,
    current_time,
    open_memory_channel,
    sleep_until,
)

from vumi2.messages import Event, EventType, Message

from .processors import ShortMessageProcesserBase, SubmitShortMessageProcesserBase
from .sequencers import Sequencer

logger = getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from .smpp import SmppTransceiverTransportConfig


class EsmeClientError(Exception):
    """Base class for all EsmeClient errors"""


class EsmeResponseStatusError(EsmeClientError):
    """Received a response PDU with non-OK status"""


class EsmeClient:
    """
    An SMPP 3.4 compatible client, for use with Vumi messages.

    Calls callback functions with vumi Messages and Events, and provides functions
    to send vumi Messages.
    """

    def __init__(
        self,
        nursery: Nursery,
        stream: SocketStream,
        config: "SmppTransceiverTransportConfig",
        sequencer: Sequencer,
        submit_sm_processor: SubmitShortMessageProcesserBase,
        sm_processer: ShortMessageProcesserBase,
        send_message_channel: MemorySendChannel,
    ) -> None:
        self.config = config
        self.stream = stream
        self.nursery = nursery
        self.sequencer = sequencer
        self.submit_sm_processor = submit_sm_processor
        self.sm_processer = sm_processer
        self.send_message_channel = send_message_channel
        self.buffer = bytearray()
        self.responses: Dict[int, MemorySendChannel] = {}
        self.encoder = PDUEncoder()

    async def start(self) -> None:
        """
        Starts the client consuming from the TCP tream, completes an SMPP bind, and
        starts the periodic sending of enquire links
        """
        # TODO: timeout on bind
        self.nursery.start_soon(self.consume_stream)
        await self.bind(
            system_id=self.config.system_id,
            password=self.config.password,
            system_type=self.config.system_type,
            interface_version=self.config.interface_version,
            address_range=self.config.address_range,
        )
        self.nursery.start_soon(self.enquire_link)

    async def enquire_link(self) -> None:
        """
        Continuously loops, periodically enquiring the link status
        """
        # TODO: timeout if we don't get a response
        while True:
            deadline = current_time() + self.config.smpp_enquire_link_interval
            pdu = EnquireLink(seqNum=await self.sequencer.get_next_sequence_number())
            await self.send_pdu(pdu)
            await sleep_until(deadline)

    async def consume_stream(self) -> None:
        """
        Consumes the stream of bytes that we're receiving, buffering them, splitting
        it out into PDUs, and then calling handle_pdu for them
        """
        async for data in self.stream:
            self.buffer.extend(data)
            while True:
                # Extract all PDUs from buffer until there are none left
                pdu_data = self.extract_pdu(self.buffer)
                if pdu_data is None:
                    break
                pdu = self.encoder.decode(BytesIO(pdu_data))
                await self.handle_pdu(pdu)

    async def handle_pdu(self, pdu: PDU) -> None:
        """
        Handles the received decoded PDUs.

        Sends requests to handler functions, eg. `unbind` will be handled by
        `self.handle_unbind`. If such a function doesn't exist, a warning is logged
        and the PDU is ignored.

        Sends responses back to the task that made the request.
        """
        logger.debug("Received PDU %s", pdu)
        # Requests must go to the handler functions
        if isinstance(pdu, PDURequest):
            command_name = pdu.commandId.name
            handler_function = getattr(self, f"handle_{command_name}", None)
            # TODO: implement handler functions for commands
            if handler_function is None:
                logger.warning(
                    "Received PDU with unknown command name %s", command_name
                )
                await self.send_pdu(
                    GenericNack(seqNum=pdu.seqNum, status=CommandStatus.ESME_RINVCMDID)
                )
                return
            await handler_function(pdu)
        # If this is a response, send the response PDU to the task waiting for it
        elif isinstance(pdu, PDUResponse):
            send_channel = self.responses.pop(pdu.seqNum)
            async with send_channel:
                await send_channel.send(pdu)
        else:
            logger.warning("Unknown PDU type, ignoring %s", pdu)

    @staticmethod
    def extract_pdu(data: bytearray) -> Optional[bytearray]:
        """
        Used for extracting PDUs from the buffer

        If there is a valid length PDU in `data`, returns a bytearray of it, and removes
        it from `data`. If not, returns None.
        """
        if len(data) < 16:
            return None
        cmd_length = int.from_bytes(data[:4], "big")
        if len(data) < cmd_length:
            return None
        pdu = data[:cmd_length]
        del data[:cmd_length]
        return pdu

    async def bind(
        self,
        system_id: str,
        password: str,
        system_type: Optional[str] = None,
        interface_version: int = 34,
        addr_ton: AddrTon = AddrTon.UNKNOWN,
        addr_npi: AddrNpi = AddrNpi.UNKNOWN,
        address_range: Optional[str] = None,
    ) -> PDU:
        """
        Sends a bind request to the server, and waits for a successful bind response
        """
        pdu = BindTransceiver(
            seqNum=await self.sequencer.get_next_sequence_number(),
            system_id=system_id,
            password=password,
            system_type=system_type,
            interface_version=interface_version,
            addr_ton=addr_ton,
            addr_npi=addr_npi,
            address_range=address_range,
        )
        bind_response = await self.send_pdu(pdu)
        logger.info("SMPP bound with response %s", bind_response)
        return bind_response

    async def send_pdu(
        self, pdu: Union[PDURequest, PDUResponse], check_response: bool = True
    ) -> Optional[PDU]:
        """
        Sends the PDU, waits for, and returns the response PDU
        """
        logger.debug("Sending PDU %s", pdu)

        if isinstance(pdu, PDUResponse):
            await self.stream.send_all(self.encoder.encode(pdu))
            return None

        send_channel, receive_channel = open_memory_channel(0)
        self.responses[pdu.seqNum] = send_channel
        await self.stream.send_all(self.encoder.encode(pdu))
        async for response in receive_channel:
            if check_response and response.status != CommandStatus.ESME_ROK:
                raise EsmeResponseStatusError(f"Received error response {response}")
            if not isinstance(response, pdu.requireAck):
                raise EsmeResponseStatusError(
                    f"Received response of incorrect type {response}"
                )
        return response

    async def send_vumi_message(self, message: Message):
        # If we encounter an error in one of the segments of a multipart message,
        # immediately send a nack and stop trying with the rest
        for pdu in await self.submit_sm_processor.handle_outbound_message(message):
            response = await self.send_pdu(pdu, check_response=False)

            # We will always get a response from a request
            response = cast(PDUResponse, response)

            if response.status != CommandStatus.ESME_ROK:
                status_code = command_status_name_map[response.status.name]
                nack_reason = command_status_value_map[status_code]["description"]
                event = Event(
                    user_message_id=message.message_id,
                    sent_message_id=message.message_id,
                    event_type=EventType.NACK,
                    nack_reason=nack_reason,
                )
                await self.send_message_channel.send(event)
                return

        event = Event(
            user_message_id=message.message_id,
            sent_message_id=message.message_id,
            event_type=EventType.ACK,
        )
        await self.send_message_channel.send(event)

    async def handle_deliver_sm(self, pdu: DeliverSM):
        """
        Extracts the inbound message from the PDU, sends it, and responds with ROK
        """
        msg = await self.sm_processer.handle_deliver_sm(pdu)
        if msg:
            await self.send_message_channel.send(msg)
        await self.send_pdu(DeliverSMResp(seqNum=pdu.seqNum))
