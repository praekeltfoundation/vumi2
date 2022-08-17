from typing import Dict, TypeVar

import pkg_resources
import sentry_sdk
from async_amqp import AmqpProtocol

from vumi2.config import BaseConfig
from vumi2.connectors import (
    CallbackType,
    ReceiveInboundConnector,
    ReceiveOutboundConnector,
)
from vumi2.errors import DuplicateConnectorError

ConnectorsType = TypeVar(
    "ConnectorsType",
    ReceiveInboundConnector,
    ReceiveOutboundConnector,
)


class BaseWorker:
    CONFIG_CLASS = BaseConfig

    def __init__(
        self, nursery, amqp_connection: AmqpProtocol, config: BaseConfig
    ) -> None:
        self.nursery = nursery
        self.connection = amqp_connection
        self.receive_inbound_connectors: Dict[str, ReceiveInboundConnector] = {}
        self.receive_outbound_connectors: Dict[str, ReceiveOutboundConnector] = {}
        self.config = config
        self._setup_sentry()

    def _setup_sentry(self):
        if not self.config.sentry_dsn:
            return

        sentry_sdk.init(
            dsn=self.config.sentry_dsn,
            release=pkg_resources.get_distribution("vumi2").version,
        )

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
        connector = ReceiveInboundConnector(
            self.nursery,
            self.connection,
            connector_name,
            self.config.worker_concurrency,
        )
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
        connector = ReceiveOutboundConnector(
            self.nursery,
            self.connection,
            connector_name,
            self.config.worker_concurrency,
        )
        await connector.setup(outbound_handler=outbound_handler)
        self.receive_outbound_connectors[connector_name] = connector
        return connector
