from enum import Enum
from typing import List, Optional

import cattrs
from attrs import Factory, define
from smpp.pdu.operations import PDU, SubmitSM
from smpp.pdu.pdu_types import (
    AddrNpi,
    AddrTon,
    DataCoding,
    DataCodingDefault,
    RegisteredDelivery,
    RegisteredDeliveryReceipt,
    RegisteredDeliverySmeOriginatedAcks,
)

from vumi2.messages import Message

from .codecs import register_codecs
from .sequencers import Sequencer

register_codecs()

# 140 bytes - 10 bytes for the user data header the SMSC is presumably going to add for
# us. This is a guess based mostly on optimism and the hope that we'll never have to
# deal with this stuff in production.
CSM_MESSAGE_SIZE = 130


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
    # TODO: Implement these multipart strategies
    message_payload = "message_payload"
    multipart_sar = "multipart_sar"
    multipart_udh = "multipart_udh"


@define
class RegisteredDeliveryConfig:
    delivery_receipt: RegisteredDeliveryReceipt = (
        RegisteredDeliveryReceipt.NO_SMSC_DELIVERY_RECEIPT_REQUESTED
    )
    sme_originated_acks: List[RegisteredDeliverySmeOriginatedAcks] = Factory(list)
    intermediate_notification: bool = False


@define
class SubmitShortMessageProcessorConfig:
    data_coding: DataCodingDefault = DataCodingDefault.SMSC_DEFAULT_ALPHABET
    multipart_handling: MultipartHandling = MultipartHandling.short_message
    service_type: Optional[str] = None
    source_addr_ton: AddrTon = AddrTon.UNKNOWN
    source_addr_npi: AddrNpi = AddrNpi.UNKNOWN
    dest_addr_ton: AddrTon = AddrTon.UNKNOWN
    dest_addr_npi: AddrNpi = AddrNpi.ISDN
    registered_delivery: RegisteredDeliveryConfig = Factory(RegisteredDeliveryConfig)
    multipart_sar_reference_rollover: int = 0x10000


class SubmitShortMessageProcesserBase:  # pragma: no cover
    def __init__(self, config: dict, sequencer: Sequencer) -> None:
        ...

    async def handle_outbound_message(self, message: Message) -> List[PDU]:
        ...


class SubmitShortMessageProcessor(SubmitShortMessageProcesserBase):
    CONFIG_CLASS = SubmitShortMessageProcessorConfig

    def __init__(self, config: dict, sequencer: Sequencer) -> None:
        self.sequencer = sequencer
        self.config = cattrs.structure(config, self.CONFIG_CLASS)

    def _get_msg_length(self, split_msg=False) -> int:
        # From https://www.twilio.com/docs/glossary/what-sms-character-limit#sms-message-length-and-character-encoding
        if self.config.data_coding in (
            DataCodingDefault.SMSC_DEFAULT_ALPHABET,
            DataCodingDefault.IA5_ASCII,
        ):
            if split_msg:
                return 153
            else:
                return 160
        if self.config.data_coding in (
            DataCodingDefault.LATIN_1,
            DataCodingDefault.CYRILLIC,
            DataCodingDefault.ISO_8859_8,
        ):
            if split_msg:
                return 134
            else:
                return 140
        if self.config.data_coding in (DataCodingDefault.JIS, DataCodingDefault.UCS2):
            if split_msg:
                return 67
            else:
                return 70
        raise ValueError(f"Unknown data coding {self.config.data_coding}")

    def _fits_in_one_message(self, content: bytes) -> bool:
        return len(content) <= self._get_msg_length()

    async def handle_outbound_message(self, message: Message) -> List[PDU]:
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
        if self.config.multipart_handling == MultipartHandling.message_payload:
            return [
                await self.submit_sm_long_message(
                    message.from_addr, message.to_addr, message_content
                )
            ]
        if self.config.multipart_handling == MultipartHandling.multipart_sar:
            return await self.submit_csm_sar_message(message.from_addr, message.to_addr, message_content)
        return []

    async def submit_sm_short_message(
        self, from_addr: str, to_addr: str, short_message: bytes
    ) -> SubmitSM:
        return SubmitSM(
            seqNum=await self.sequencer.get_next_sequence_number(),
            service_type=self.config.service_type,
            source_addr_ton=self.config.source_addr_ton,
            source_addr_npi=self.config.source_addr_npi,
            source_addr=from_addr,
            dest_addr_ton=self.config.dest_addr_ton,
            dest_addr_npi=self.config.dest_addr_npi,
            destination_addr=to_addr,
            data_coding=DataCoding(schemeData=self.config.data_coding),
            registered_delivery=RegisteredDelivery(
                self.config.registered_delivery.delivery_receipt,
                self.config.registered_delivery.sme_originated_acks,
                self.config.registered_delivery.intermediate_notification,
            ),
            short_message=short_message,
        )

    async def submit_sm_long_message(
        self, from_addr: str, to_addr: str, message: bytes
    ) -> SubmitSM:
        return SubmitSM(
            seqNum=await self.sequencer.get_next_sequence_number(),
            service_type=self.config.service_type,
            source_addr_ton=self.config.source_addr_ton,
            source_addr_npi=self.config.source_addr_npi,
            source_addr=from_addr,
            dest_addr_ton=self.config.dest_addr_ton,
            dest_addr_npi=self.config.dest_addr_npi,
            destination_addr=to_addr,
            data_coding=DataCoding(schemeData=self.config.data_coding),
            registered_delivery=RegisteredDelivery(
                self.config.registered_delivery.delivery_receipt,
                self.config.registered_delivery.sme_originated_acks,
                self.config.registered_delivery.intermediate_notification,
            ),
            message_payload=message,
        )

    def _split_message(self, message: bytes, size: int):
        for i in range(0, len(message), size):
            yield message[i: i + size]

    async def submit_csm_sar_message(
        self, from_addr: str, to_addr: str, message: bytes
    ) -> List[SubmitSM]:
        pdus = []
        ref_num = (await self.sequencer.get_next_sequence_number()) % self.config.multipart_sar_reference_rollover
        segments = list(self._split_message(message, self._get_msg_length(split_msg=True)))
        for i, segment in enumerate(segments):
            pdus.append(SubmitSM(
                seqNum=await self.sequencer.get_next_sequence_number(),
                service_type=self.config.service_type,
                source_addr_ton=self.config.source_addr_ton,
                source_addr_npi=self.config.source_addr_npi,
                source_addr=from_addr,
                dest_addr_ton=self.config.dest_addr_ton,
                dest_addr_npi=self.config.dest_addr_npi,
                destination_addr=to_addr,
                data_coding=DataCoding(schemeData=self.config.data_coding),
                registered_delivery=RegisteredDelivery(
                    self.config.registered_delivery.delivery_receipt,
                    self.config.registered_delivery.sme_originated_acks,
                    self.config.registered_delivery.intermediate_notification,
                ),
                sar_msg_ref_num=ref_num,
                sar_total_segments=len(segments),
                sar_segment_seqnum=i + 1,
                short_message=segment,
            ))
        return pdus

