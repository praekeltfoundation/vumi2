from logging import getLogger
from typing import Optional

from async_amqp import AmqpProtocol
from attrs import Factory, define
from trio import MemoryReceiveChannel, Nursery, open_memory_channel, open_tcp_stream

from vumi2.cli import class_from_string
from vumi2.messages import Event, Message
from vumi2.workers import BaseConfig, BaseWorker

from .client import EsmeClient

logger = getLogger(__name__)


@define
class SmppTransceiverTransportConfig(BaseConfig):
    transport_name: str = "smpp"
    host: str = "localhost"
    port: int = 2775
    system_id: str = "smppclient1"
    password: str = "password"
    system_type: Optional[str] = None
    interface_version: int = 34
    address_range: Optional[str] = None
    smpp_enquire_link_interval: int = 55
    sequencer_class: str = "vumi2.transports.smpp.sequencers.InMemorySequencer"
    sequencer_config: dict = Factory(dict)
    submit_sm_processor_class: str = (
        "vumi2.transports.smpp.processors.SubmitShortMessageProcessor"
    )
    submit_sm_processor_config: dict = Factory(dict)
    sm_processor_class: str = "vumi2.transports.smpp.processors.ShortMessageProcessor"
    sm_processor_config: dict = Factory(dict)
    dr_processor_class: str = "vumi2.transports.smpp.processors.DeliveryReportProcesser"
    dr_processor_config: dict = Factory(dict)
    smpp_cache_class: str = "vumi2.transports.smpp.smpp_cache.InMemorySmppCache"
    smpp_cache_config: dict = Factory(dict)
    smpp_bind_timeout: int = 30


class SmppTransceiverTransport(BaseWorker):
    CONFIG_CLASS = SmppTransceiverTransportConfig

    def __init__(
        self,
        nursery: Nursery,
        amqp_connection: AmqpProtocol,
        config: SmppTransceiverTransportConfig,
    ) -> None:
        super().__init__(nursery, amqp_connection, config)
        self.config: SmppTransceiverTransportConfig = config
        sequencer_class = class_from_string(config.sequencer_class)
        self.sequencer = sequencer_class(config.sequencer_config)
        submit_sm_processor_class = class_from_string(config.submit_sm_processor_class)
        self.submit_sm_processor = submit_sm_processor_class(
            self.config.submit_sm_processor_config, self.sequencer
        )
        smpp_cache_class = class_from_string(config.smpp_cache_class)
        self.smpp_cache = smpp_cache_class(config.smpp_cache_config)
        sm_processor_class = class_from_string(config.sm_processor_class)
        self.sm_processor = sm_processor_class(
            self.config.sm_processor_config, self.smpp_cache
        )
        dr_processor_class = class_from_string(config.dr_processor_class)
        self.dr_processor = dr_processor_class(
            config.dr_processor_config, self.smpp_cache
        )

    async def setup(self) -> None:
        # We open the TCP connection first, so that we have a place to send any
        # outbounds once we start receiving them from the AMQP server
        self.stream = await open_tcp_stream(
            host=self.config.host, port=self.config.port
        )
        send_channel, receive_channel = open_memory_channel(0)
        self.client = EsmeClient(
            self.nursery,
            self.stream,
            self.config,
            self.sequencer,
            self.smpp_cache,
            self.submit_sm_processor,
            self.sm_processor,
            self.dr_processor,
            send_channel,
        )
        await self.client.start()
        self.connector = await self.setup_receive_outbound_connector(
            connector_name=self.config.transport_name,
            outbound_handler=self.handle_outbound,
        )
        self.nursery.start_soon(self.handle_inbound_message_or_event, receive_channel)

    async def handle_inbound_message_or_event(
        self, receive_message_channel: MemoryReceiveChannel
    ) -> None:
        async for msg in receive_message_channel:
            if isinstance(msg, Event):
                await self.connector.publish_event(msg)
            elif isinstance(msg, Message):
                msg.transport_name = self.config.transport_name
                await self.connector.publish_inbound(msg)
            else:
                logger.error(f"Received invalid message type {type(msg)}")

    async def handle_outbound(self, message: Message) -> None:
        await self.client.send_vumi_message(message)
