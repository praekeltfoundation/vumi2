from typing import Dict, TypeVar

from async_amqp import AmqpProtocol

from vumi2.connectors import ReceiveInboundConnector, ReceiveOutboundConnector
from vumi2.errors import DuplicateConnectorError
from vumi2.services import CallbackType

ConnectorsType = TypeVar(
    "ConnectorsType",
    ReceiveInboundConnector,
    ReceiveOutboundConnector,
)


class BaseWorker:
    def __init__(self, amqp_connection: AmqpProtocol):
        self.connection = amqp_connection
        self.receive_inbound_connectors: Dict[str, ReceiveInboundConnector] = {}
        self.receive_outbound_connectors: Dict[str, ReceiveOutboundConnector] = {}

    async def setup(self):
        pass

    async def setup_receive_inbound_connector(
        self,
        connector_name: str,
        inbound_handler: CallbackType,
        event_handler: CallbackType,
    ) -> ReceiveInboundConnector:
        if connector_name in self.receive_inbound_connectors:
            raise DuplicateConnectorError(
                "Attempt to add duplicate receive inbound connector with name"
                f" {connector_name}"
            )
        connector = ReceiveInboundConnector(self.connection, connector_name)
        await connector.setup(
            inbound_handler=inbound_handler, event_handler=event_handler
        )
        self.receive_inbound_connectors[connector_name] = connector
        return connector

    async def setup_receive_outbound_connector(
        self, connector_name: str, outbound_handler: CallbackType
    ) -> ReceiveOutboundConnector:
        if connector_name in self.receive_outbound_connectors:
            raise DuplicateConnectorError(
                "Attempt to add duplicate receive outbound connector with name"
                f" {connector_name}"
            )
        connector = ReceiveOutboundConnector(self.connection, connector_name)
        await connector.setup(outbound_handler=outbound_handler)
        self.receive_outbound_connectors[connector_name] = connector
        return connector
