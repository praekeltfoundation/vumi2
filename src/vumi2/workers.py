from typing import Dict, Type, TypeVar

from async_amqp import AmqpProtocol

from vumi2.connectors import ReceiveInboundConnector, ReceiveOutboundConnector
from vumi2.errors import DuplicateConnectorError

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

    async def _setup_connector(
        self, store: dict, connector_cls: Type[ConnectorsType], connector_name: str
    ) -> ConnectorsType:
        if connector_name in store:
            raise DuplicateConnectorError(
                f"Attempt to add duplicate connector with name {connector_name}"
            )
        connector = connector_cls(self.connection, connector_name)
        await connector.setup()
        store[connector_name] = connector
        return connector

    async def setup_receive_inbound_connector(
        self, connector_name: str
    ) -> ReceiveInboundConnector:
        return await self._setup_connector(
            self.receive_inbound_connectors, ReceiveInboundConnector, connector_name
        )

    async def setup_receive_outbound_connector(
        self, connector_name: str
    ) -> ReceiveOutboundConnector:
        return await self._setup_connector(
            self.receive_outbound_connectors, ReceiveOutboundConnector, connector_name
        )
