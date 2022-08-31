from logging import getLogger
from typing import Optional

from async_amqp import AmqpProtocol
from attrs import define
from trio import Nursery, open_tcp_stream

from vumi2.messages import Message
from vumi2.workers import BaseConfig, BaseWorker

from .client import EsmeClient

logger = getLogger(__name__)


@define
class SmppTransceiverTransportConfig(BaseConfig):
    transport_name: str = "sms"
    host: str = "localhost"
    port: int = 2775
    system_id: str = "smppclient1"
    password: str = "password"
    system_type: Optional[str] = None
    interface_version: int = 34
    address_range: str = ""
    smpp_enquire_link_interval: int = 55


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

    async def setup(self) -> None:
        # We open the TCP connection first, so that we have a place to send any
        # outbounds once we start receiving them from the AMQP server
        self.stream = await open_tcp_stream(
            host=self.config.host, port=self.config.port
        )
        self.client = EsmeClient(self.nursery, self.stream, self.config)
        await self.client.start()
        self.connector = await self.setup_receive_outbound_connector(
            connector_name=self.config.transport_name,
            outbound_handler=self.handle_outbound,
        )

    async def handle_outbound(self, message: Message) -> None:  # pragma: no cover
        # TODO: implement sending outbound messages to client
        pass