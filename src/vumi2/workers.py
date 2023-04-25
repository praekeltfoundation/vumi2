import importlib.metadata
from logging import getLogger
from typing import TypedDict, TypeVar, get_type_hints

import sentry_sdk
from async_amqp import AmqpProtocol  # type: ignore
from async_amqp.protocol import CLOSED, CLOSING, CONNECTING, OPEN  # type: ignore
from hypercorn import Config as HypercornConfig
from hypercorn.trio import serve as hypercorn_serve
from quart_trio import QuartTrio
from trio import Nursery, open_nursery
from trio.abc import AsyncResource

from vumi2.config import BaseConfig
from vumi2.connectors import (
    ConnectorCollection,
    EventCallbackType,
    MessageCallbackType,
    ReceiveInboundConnector,
    ReceiveOutboundConnector,
)
from vumi2.errors import DuplicateConnectorError

ConnectorsType = TypeVar(
    "ConnectorsType",
    ReceiveInboundConnector,
    ReceiveOutboundConnector,
)

logger = getLogger(__name__)


class HealthCheckResp(TypedDict):
    health: str
    components: dict[str, str]


class BaseWorker(AsyncResource):
    config: BaseConfig

    @classmethod
    def get_config_class(cls):
        return get_type_hints(cls)["config"]

    def __init__(
        self, nursery: Nursery, amqp_connection: AmqpProtocol, config: BaseConfig
    ) -> None:
        logger.info(
            "Starting %s worker with config %s", self.__class__.__name__, config
        )
        self.nursery = nursery
        self.connection = amqp_connection
        self.receive_inbound_connectors: dict[str, ReceiveInboundConnector] = {}
        self.receive_outbound_connectors: dict[str, ReceiveOutboundConnector] = {}
        self.config = config
        self._setup_sentry()
        self.healthchecks = {"amqp": self._amqp_healthcheck}
        if config.http_bind is not None:
            self._setup_http(config.http_bind)
        self._connectors = ConnectorCollection()
        self.resources_to_close: list[AsyncResource] = [self._connectors]

    def _setup_sentry(self):
        if not self.config.sentry_dsn:
            return

        sentry_sdk.init(
            dsn=self.config.sentry_dsn,
            release=importlib.metadata.distribution("vumi2").version,
        )

    def _setup_http(self, http_bind: str) -> None:
        self.http_app = QuartTrio(__name__)
        http_config = HypercornConfig()
        http_config.bind = [http_bind]
        http_config.backlog = self.config.worker_concurrency
        self.nursery.start_soon(hypercorn_serve, self.http_app, http_config)
        self.http_app.add_url_rule("/health", view_func=self._healthcheck_request)

    async def _healthcheck_request(self):
        response: HealthCheckResp = {"health": "ok", "components": {}}
        for name, function in self.healthchecks.items():
            result = await function()
            if result["health"] != "ok":
                response["health"] = "down"
            response["components"][name] = result
        return response, 200 if response["health"] == "ok" else 500

    async def _amqp_healthcheck(self):
        result = {
            "health": "ok",
            "server_properties": self.connection.server_properties,
        }
        state = {
            CONNECTING: "connecting",
            OPEN: "open",
            CLOSING: "closing",
            CLOSED: "closed",
        }[self.connection.state]
        result["state"] = state
        if state != "open":  # pragma: no cover
            result["health"] = "down"
        return result

    async def aclose(self):
        async with open_nursery() as nursery:
            for resource in self.resources_to_close:
                nursery.start_soon(resource.aclose)
            # The nursery will block until all resources are closed.

    async def setup(self):
        pass

    async def setup_receive_inbound_connector(
        self,
        connector_name: str,
        inbound_handler: MessageCallbackType,
        event_handler: EventCallbackType,
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
        self._connectors.add(connector)
        await connector.setup(
            inbound_handler=inbound_handler, event_handler=event_handler
        )
        self.receive_inbound_connectors[connector_name] = connector
        return connector

    async def setup_receive_outbound_connector(
        self, connector_name: str, outbound_handler: MessageCallbackType
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
        self._connectors.add(connector)
        await connector.setup(outbound_handler=outbound_handler)
        self.receive_outbound_connectors[connector_name] = connector
        return connector
