import re
from collections.abc import Callable
from enum import Enum
from logging import getLogger
from typing import TypeVar

from attrs import Factory, define, field
from smpp.pdu.operations import PDU, DeliverSM, SubmitSM  # type: ignore
from smpp.pdu.pdu_types import (  # type: ignore
    AddrNpi,
    AddrTon,
    DataCoding,
    DataCodingDefault,
    EsmClassType,
    RegisteredDelivery,
    RegisteredDeliveryReceipt,
    RegisteredDeliverySmeOriginatedAcks,
)

from vumi2.config import structure_config
from vumi2.messages import DeliveryStatus, Event, EventType, Message, TransportType

from .codecs import register_codecs
from .sequencers import Sequencer
from .smpp_cache import BaseSmppCache

register_codecs()

logger = getLogger(__name__)

ET = TypeVar("ET", bound=Enum)


def convert_enum(enum: type[ET]) -> Callable[[int | str | ET], ET]:
    def _convert_enum(value: int | str | ET) -> ET:
        if isinstance(value, int):
            raise TypeError("Enums must be specified by name")
        if isinstance(value, str):
            return enum[value]
        return value

    return _convert_enum


def conv_enum_list(enum: type[ET]) -> Callable[[list[int | str | ET]], list[ET]]:
    ce = convert_enum(enum)

    def _conv_enum_list(values: list[int | str | ET]) -> list[ET]:
        return [ce(val) for val in values]

    return _conv_enum_list


def enum_field(enum: type[ET], **kw):
    return field(converter=convert_enum(enum), **kw)


def enum_list_field(enum: type[ET], **kw):
    return field(converter=conv_enum_list(enum), **kw)


class DataCodingCodecs(Enum):
    SMSC_DEFAULT_ALPHABET = "gsm0338"  # SMSC Default Alphabet
    IA5_ASCII = "ascii"  # IA5 (CCITT T.50)/ASCII (ANSI X3.4)
    # 0x02: Octet unspecified (8-bit binary)
    LATIN_1 = "latin1"  # Latin 1 (ISO-8859-1)
    # 0x04: Octet unspecified (8-bit binary)
    # http://www.herongyang.com/Unicode/JIS-ISO-2022-JP-Encoding.html
    JIS = "iso2022_jp"  # JIS (X 0208-1990)
    CYRILLIC = "iso8859_5"  # Cyrllic (ISO-8859-5)
    ISO_8859_8 = "iso8859_8"  # Latin/Hebrew (ISO-8859-8)
    UCS2 = "utf_16_be"  # UCS2 (ISO/IEC-10646)
    # TODO: Do we need to add support for these? Original vumi didn't
    # 0x09: Pictogram Encoding
    # 0x0A: ISO-2022-JP (Music Codes)
    # 0x0B: reserved
    # 0x0C: reserved
    # 0x0D: Extended Kanji JIS(X 0212-1990)
    # 0x0E: KS C 5601
    # 0x0F: reserved


class MultipartHandling(Enum):
    short_message = "short_message"
    message_payload = "message_payload"
    multipart_sar = "multipart_sar"
    multipart_udh = "multipart_udh"


@define
class RegisteredDeliveryConfig:
    delivery_receipt: RegisteredDeliveryReceipt = enum_field(
        RegisteredDeliveryReceipt,
        default=RegisteredDeliveryReceipt.NO_SMSC_DELIVERY_RECEIPT_REQUESTED,
    )
    sme_originated_acks: list[RegisteredDeliverySmeOriginatedAcks] = enum_list_field(
        RegisteredDeliverySmeOriginatedAcks,
        factory=list,
    )
    intermediate_notification: bool = False


@define
class SubmitShortMessageProcessorConfig:
    data_coding: DataCodingDefault = enum_field(
        DataCodingDefault,
        default=DataCodingDefault.SMSC_DEFAULT_ALPHABET,
    )
    multipart_handling: MultipartHandling = MultipartHandling.short_message
    service_type: str | None = None
    source_addr_ton: AddrTon = enum_field(AddrTon, default=AddrTon.UNKNOWN)
    source_addr_npi: AddrNpi = enum_field(AddrNpi, default=AddrNpi.UNKNOWN)
    dest_addr_ton: AddrTon = enum_field(AddrTon, default=AddrTon.UNKNOWN)
    dest_addr_npi: AddrNpi = enum_field(AddrNpi, default=AddrNpi.ISDN)
    registered_delivery: RegisteredDeliveryConfig = Factory(RegisteredDeliveryConfig)
    multipart_sar_reference_rollover: int = 0x10000


class SubmitShortMessageProcesserBase:  # pragma: no cover
    def __init__(self, config: dict, sequencer: Sequencer) -> None:
        ...

    async def handle_outbound_message(  # type: ignore
        self,
        message: Message,
    ) -> list[PDU]:
        ...


class SubmitShortMessageProcessor(SubmitShortMessageProcesserBase):
    config: SubmitShortMessageProcessorConfig

    def __init__(self, config: dict, sequencer: Sequencer) -> None:
        self.sequencer = sequencer
        self.config = structure_config(config, self)

    def _get_msg_length(self, split_msg=False) -> int:
        # From https://www.twilio.com/docs/glossary/what-sms-character-limit
        # Also in 3GPP TS 23.040 version 16.0.0 Release 16 , page 77, section 9.2.3.24.1
        # https://www.etsi.org/deliver/etsi_ts/123000_123099/123040/16.00.00_60/ts_123040v160000p.pdf
        if self.config.data_coding in (
            DataCodingDefault.SMSC_DEFAULT_ALPHABET,
            DataCodingDefault.IA5_ASCII,
        ):
            if split_msg:
                return 153
            else:
                return 160
        else:
            if split_msg:
                return 134
            else:
                return 140

    def _fits_in_one_message(self, content: bytes) -> bool:
        return len(content) <= self._get_msg_length()

    async def handle_outbound_message(self, message: Message) -> list[SubmitSM]:
        """
        Takes an outbound vumi message, and returns the PDUs necessary to send it.
        """
        # TODO: support USSD over SMPP
        codec = DataCodingCodecs[self.config.data_coding.name]
        message_content = (message.content or "").encode(codec.value)

        if (
            self.config.multipart_handling == MultipartHandling.short_message
            or self._fits_in_one_message(message_content)
        ):
            return [
                await self.submit_sm_short_message(
                    message.from_addr, message.to_addr, message_content
                )
            ]
        elif self.config.multipart_handling == MultipartHandling.message_payload:
            return [
                await self.submit_sm_long_message(
                    message.from_addr, message.to_addr, message_content
                )
            ]
        elif self.config.multipart_handling == MultipartHandling.multipart_sar:
            return await self.submit_csm_sar_message(
                message.from_addr, message.to_addr, message_content
            )
        else:  # multipart_udh:
            return await self.submit_csm_udh_message(
                message.from_addr, message.to_addr, message_content
            )

    async def submit_sm_short_message(
        self, from_addr: str, to_addr: str, short_message: bytes
    ) -> SubmitSM:
        return await self._submit_sm(
            source_addr=from_addr, destination_addr=to_addr, short_message=short_message
        )

    async def submit_sm_long_message(
        self, from_addr: str, to_addr: str, message: bytes
    ) -> SubmitSM:
        return await self._submit_sm(
            source_addr=from_addr, destination_addr=to_addr, message_payload=message
        )

    def _split_message(self, message: bytes, size: int):
        for i in range(0, len(message), size):
            yield message[i : i + size]

    async def submit_csm_sar_message(
        self, from_addr: str, to_addr: str, message: bytes
    ) -> list[SubmitSM]:
        pdus = []
        ref_num = (
            await self.sequencer.get_next_sequence_number()
        ) % self.config.multipart_sar_reference_rollover
        segments = list(
            self._split_message(message, self._get_msg_length(split_msg=True))
        )
        for i, segment in enumerate(segments):
            pdus.append(
                await self._submit_sm(
                    source_addr=from_addr,
                    destination_addr=to_addr,
                    sar_msg_ref_num=ref_num,
                    sar_total_segments=len(segments),
                    sar_segment_seqnum=i + 1,
                    short_message=segment,
                )
            )
        return pdus

    async def submit_csm_udh_message(
        self, from_addr: str, to_addr: str, message: bytes
    ) -> list[SubmitSM]:
        pdus = []
        ref_num = (await self.sequencer.get_next_sequence_number()) % 0xFF
        segments = list(
            self._split_message(message, self._get_msg_length(split_msg=True))
        )
        for i, segment in enumerate(segments):
            short_message = b"".join(
                [
                    b"\05",  # UDH header length
                    b"\00",  # Information Element Identifier for concatenated SMS
                    b"\03",  # Header length
                    bytes([ref_num]),  # Concatenated SMS reference number
                    bytes([len(segments)]),  # Total number of parts
                    bytes([i + 1]),  # This part's number in the sequence
                    segment,
                ]
            )
            pdus.append(
                await self._submit_sm(
                    source_addr=from_addr,
                    destination_addr=to_addr,
                    short_message=short_message,
                )
            )
        return pdus

    async def _submit_sm(self, **kwargs):
        """
        Handles all the fields that are from the config and hence common to all
        SubmitSMs. `kwargs` are the additional fields to add
        """
        return SubmitSM(
            seqNum=await self.sequencer.get_next_sequence_number(),
            service_type=self.config.service_type,
            source_addr_ton=self.config.source_addr_ton,
            source_addr_npi=self.config.source_addr_npi,
            dest_addr_ton=self.config.dest_addr_ton,
            dest_addr_npi=self.config.dest_addr_npi,
            data_coding=DataCoding(schemeData=self.config.data_coding),
            registered_delivery=RegisteredDelivery(
                self.config.registered_delivery.delivery_receipt,
                self.config.registered_delivery.sme_originated_acks,
                self.config.registered_delivery.intermediate_notification,
            ),
            **kwargs,
        )


class DeliveryReportProcesserBase:  # pragma: no cover
    def __init__(self, config: dict) -> None:
        ...

    async def handle_deliver_sm(  # type: ignore
        self,
        pdu: DeliverSM,
    ) -> tuple[bool, Event | None]:
        ...


DELIVERY_REPORT_REGEX = (
    r"id:(?P<id>[^ ]{,65})"
    r"(?: +sub:(?P<sub>[^ ]+))?"
    r"(?: +dlvrd:(?P<dlvrd>[^ ]+))?"
    r"(?: +submit date:(?P<submit_date>\d*))?"
    r"(?: +done date:(?P<done_date>\d*))?"
    r" +stat:(?P<stat>[A-Z]{5,7})"
    r"(?: +err:(?P<err>[^ ]+))?"
    r" +[Tt]ext:(?P<text>.{,20})"
    r".*"
)

DELIVERY_REPORT_STATUS_MAPPING = {
    # SMPP `message_state` values:
    "ENROUTE": "pending",
    "DELIVERED": "delivered",
    "EXPIRED": "failed",
    "DELETED": "failed",
    "UNDELIVERABLE": "failed",
    "ACCEPTED": "delivered",
    "UNKNOWN": "pending",
    "REJECTED": "failed",
    # From the most common regex-extracted format:
    "DELIVRD": "delivered",
    "REJECTD": "failed",
    "FAILED": "failed",
    # Currently we will accept this for Yo! TODO: investigate
    "0": "delivered",
}


@define
class DeliveryReportProcessorConfig:
    regex: str = DELIVERY_REPORT_REGEX
    status_mapping: dict = DELIVERY_REPORT_STATUS_MAPPING


class DeliveryReportProcesser(DeliveryReportProcesserBase):
    config: DeliveryReportProcessorConfig

    def __init__(self, config: dict, smpp_cache: BaseSmppCache) -> None:
        self.config = structure_config(config, self)
        self.regex = re.compile(self.config.regex)
        self.smpp_cache = smpp_cache

    async def _handle_deliver_sm_optional_params(
        self, pdu: DeliverSM
    ) -> tuple[bool, Event | None]:
        """
        Check if this is a delivery report using the optional PDU params.

        If so, return the equivalent vumi Event, otherwise return None.
        """
        receipted_message_id = pdu.params.get("receipted_message_id")
        message_state = pdu.params.get("message_state")
        if receipted_message_id is None or message_state is None:
            return False, None
        return True, await self._create_event(
            receipted_message_id.decode(), message_state.name
        )

    async def _handle_deliver_sm_esm_class(
        self, pdu: DeliverSM
    ) -> tuple[bool, Event | None]:
        """
        Check if this is a delivery report by looking at the esm_class.

        If so, return the equivalent vumi Event, otherwise return None.
        """
        esm_class = pdu.params["esm_class"]
        # Any type other than default is delivery report
        if esm_class.type == EsmClassType.DEFAULT:
            return False, None

        # The SMPP spec doesn't mention encodings at all for
        # delivery reports, so assume they're plain ASCII and decode
        # with latin1 to avoid decode errors.

        content = pdu.params["short_message"].decode("latin1")
        match = self.regex.match(content)
        if not match:
            logger.warning(
                (
                    "esm_class %s indicates delivery report, but regex does not match"
                    " content: %s"
                ),
                esm_class.type.name,
                content,
            )
            return False, None

        fields = match.groupdict()
        return True, await self._create_event(fields["id"], fields["stat"])

    async def _handle_deliver_sm_body(
        self, pdu: DeliverSM
    ) -> tuple[bool, Event | None]:
        """
        Try to decode the body as a delivery report, even if the esm_class doesn't
        say it's a delivery report
        """
        content = pdu.params["short_message"].decode("latin1")
        match = self.regex.match(content)
        if not match:
            return False, None

        fields = match.groupdict()
        return True, await self._create_event(fields["id"], fields["stat"])

    async def _create_event(
        self, smpp_message_id: str, smpp_status: str
    ) -> Event | None:
        status = DeliveryStatus(self.config.status_mapping.get(smpp_status, "pending"))
        vumi_message_id = await self.smpp_cache.get_smpp_message_id(smpp_message_id)
        if not vumi_message_id:
            logger.warning(
                (
                    "Unable to find message ID %s in SMPP cache, not sending status"
                    " update %s"
                ),
                smpp_message_id,
                smpp_status,
            )
            return None
        if status in (DeliveryStatus.DELIVERED, DeliveryStatus.FAILED):
            await self.smpp_cache.delete_smpp_message_id(smpp_message_id)
        return Event(
            user_message_id=vumi_message_id,
            event_type=EventType.DELIVERY_REPORT,
            delivery_status=status,
            transport_metadata={"smpp_delivery_status": smpp_status},
        )

    async def handle_deliver_sm(self, pdu: DeliverSM) -> tuple[bool, Event | None]:
        """
        Try to handle the pdu as a delivery report. Returns an equivalent Event if
        handled, or None if not.
        """
        for func in (
            self._handle_deliver_sm_optional_params,
            self._handle_deliver_sm_esm_class,
            self._handle_deliver_sm_body,
        ):
            handled, event = await func(pdu)
            if handled:
                return handled, event
        return False, None


class ShortMessageProcesserBase:  # pragma: no cover
    def __init__(self, config: dict, smpp_cache: BaseSmppCache) -> None:
        ...

    async def handle_deliver_sm(self, pdu: DeliverSM) -> Message | None:
        ...


@define
class ShortMessageProcessorConfig:
    data_coding_overrides: dict = Factory(dict)


class ShortMessageProcessor(ShortMessageProcesserBase):
    config: ShortMessageProcessorConfig

    def __init__(self, config: dict, smpp_cache: BaseSmppCache) -> None:
        self.config = structure_config(config, self)
        self.smpp_cache = smpp_cache

    def _decode_text(self, text: bytes, data_coding: DataCoding) -> str:
        data_coding = data_coding.schemeData.name
        try:
            codec = self.config.data_coding_overrides[data_coding]
        except KeyError:
            codec = DataCodingCodecs[data_coding].value
        return text.decode(codec)

    def _get_text(self, pdu: DeliverSM) -> str:
        message_payload = pdu.params.get("message_payload")
        if message_payload is not None:
            short_message = message_payload
        else:
            short_message = pdu.params["short_message"]

        return self._decode_text(short_message, pdu.params["data_coding"])

    def _handle_short_message(self, pdu: DeliverSM) -> Message:
        """
        Single part short message
        """
        return Message(
            to_addr=pdu.params["destination_addr"].decode(),
            from_addr=pdu.params["source_addr"].decode(),
            # The transport needs to fill this in
            transport_name="",
            transport_type=TransportType.SMS,
            content=self._get_text(pdu),
        )

    def _extract_multipart(self, pdu: DeliverSM) -> tuple[int, int, int, bytes] | None:
        """
        Tries to extract the multipart data from the PDU, using optional params, or UDH
        CSM or CSM16.

        If no multipart data can be extracted, returns None, otherwise returns a tuple
        of reference_number, total_number, part_number, part_message.
        """
        # Optional params
        reference_number = pdu.params.get("sar_msg_ref_num")
        total_number = pdu.params.get("sar_total_segments")
        part_number = pdu.params.get("sar_segment_seqnum")
        short_message = pdu.params["short_message"]
        if all(i is not None for i in (reference_number, total_number, part_number)):
            return reference_number, total_number, part_number, short_message

        if short_message is None:
            return None

        # From 3GPP TS 23.040 version 16.0.0 Release 16
        # https://www.etsi.org/deliver/etsi_ts/123000_123099/123040/16.00.00_60/ts_123040v160000p.pdf
        # Page 76

        # CSM
        if len(short_message) >= 6 and short_message[:3] == b"\x05\x00\x03":
            reference_number = int.from_bytes(short_message[3:4], "big")
            total_number = int.from_bytes(short_message[4:5], "big")
            part_number = int.from_bytes(short_message[5:6], "big")
            if total_number >= 1 and 1 <= part_number <= total_number:
                part_message = short_message[6:]
                return reference_number, total_number, part_number, part_message

        # CSM16
        if len(short_message) >= 7 and short_message[:3] == b"\x06\x08\x04":
            reference_number = int.from_bytes(short_message[3:5], "big")
            total_number = int.from_bytes(short_message[5:6], "big")
            part_number = int.from_bytes(short_message[6:7], "big")
            if total_number >= 1 and 1 <= part_number <= total_number:
                part_message = short_message[7:]
                return reference_number, total_number, part_number, part_message

        return None

    async def _handle_multipart_message(
        self, pdu: DeliverSM
    ) -> tuple[bool, Message | None]:
        """
        Tries to handle the message as a multipart message.

        Returns a Tuple of whether the PDU was handled, and an optional message if we
        have all the message parts
        """
        extracted = self._extract_multipart(pdu)
        if extracted is None:
            return False, None

        ref_num, tot_num, part_num, content = extracted
        decoded_content = self._decode_text(content, pdu.params["data_coding"])
        full_message = await self.smpp_cache.store_multipart(
            ref_num, tot_num, part_num, decoded_content
        )
        if full_message is None:
            return True, None

        return True, Message(
            to_addr=pdu.params["destination_addr"].decode(),
            from_addr=pdu.params["source_addr"].decode(),
            # The transport needs to fill this in
            transport_name="",
            transport_type=TransportType.SMS,
            content=full_message,
        )

    async def handle_deliver_sm(self, pdu: DeliverSM) -> Message | None:
        """
        Processes the DeliverSM pdu, and returns a Message if one was decoded, else
        returns None.
        """
        handled, msg = await self._handle_multipart_message(pdu)
        if handled:
            return msg
        return self._handle_short_message(pdu)
