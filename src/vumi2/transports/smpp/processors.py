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

from ...messages import Message
from .codecs import register_codecs
from .sequencers import Sequencer

register_codecs()


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


class SubmitShortMessageProcessor:
    CONFIG_CLASS = SubmitShortMessageProcessorConfig

    def __init__(self, config: dict, sequencer: Sequencer) -> None:
        self.sequencer = sequencer
        self.config = cattrs.structure(config, self.CONFIG_CLASS)

    async def handle_outbound_message(self, message: Message) -> List[PDU]:
        """
        Takes an outbound vumi message, and returns the PDUs necessary to send it.
        """
        # TODO: support USSD over SMPP
        codec = DataCodingCodecs[self.config.data_coding.name]
        short_message = (message.content or "").encode(codec.value)

        registered_delivery = RegisteredDelivery(
            self.config.registered_delivery.delivery_receipt,
            self.config.registered_delivery.sme_originated_acks,
            self.config.registered_delivery.intermediate_notification,
        )

        return [
            SubmitSM(
                seqNum=await self.sequencer.get_next_sequence_number(),
                service_type=self.config.service_type,
                source_addr_ton=self.config.source_addr_ton,
                source_addr_npi=self.config.source_addr_npi,
                source_addr=message.from_addr,
                dest_addr_ton=self.config.dest_addr_ton,
                dest_addr_npi=self.config.dest_addr_npi,
                destination_addr=message.to_addr,
                data_coding=DataCoding(schemeData=self.config.data_coding),
                registered_delivery=registered_delivery,
                short_message=short_message,
            )
        ]
