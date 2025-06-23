import importlib.metadata
from functools import wraps
from logging import getLogger
from typing import TypedDict, TypeVar

import sentry_sdk
import trio
from async_amqp import AmqpProtocol  # type: ignore
from async_amqp.protocol import CLOSED, CLOSING, CONNECTING, OPEN  # type: ignore
from hypercorn import Config as HypercornConfig
from hypercorn.trio import serve as hc_serve
from quart_trio import QuartTrio
from trio.abc import AsyncResource

from attrs import asdict

from vumi2.class_helpers import class_from_string
from vumi2.config import BaseConfig
from vumi2.middlewares.base import BaseMiddlewareConfig
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


class WorkerHttp(AsyncResource):
    def __init__(self, http_bind: str, backlog: int):
        self.app = QuartTrio(__name__)
        self.config = HypercornConfig()
        self.config.bind = [http_bind]
        self.config.backlog = backlog
        self._shutdown = trio.Event()
        self._closed = trio.Event()

    async def _run(self):
        await hc_serve(self.app, self.config, shutdown_trigger=self._shutdown.wait)
        self._closed.set()

    def start(self, nursery: trio.Nursery) -> None:
        nursery.start_soon(self._run)

    async def aclose(self):
        self._shutdown.set()
        await self._closed.wait()


class BaseWorker(AsyncResource):
    config: BaseConfig

    def __init__(
        self, nursery: trio.Nursery, amqp_connection: AmqpProtocol, config: BaseConfig
    ) -> None:
        logger.info(
            "Starting %s worker with config %s", self.__class__.__name__, config
        )
        self.nursery = nursery
        self.connection = amqp_connection
        self.receive_inbound_connectors: dict[str, ReceiveInboundConnector] = {}
        self.receive_outbound_connectors: dict[str, ReceiveOutboundConnector] = {}
        self._connectors = ConnectorCollection()
        self.resources_to_close: list[AsyncResource] = [self._connectors]
        self._closed = trio.Event()
        self.config = config
        self._setup_sentry()
        self.healthchecks = {"amqp": self._amqp_healthcheck}
        if config.http_bind is not None:
            self._setup_http(config.http_bind)
        self.middlewares = []
        for middleware_config in self.config.middlewares:
            middleware_class = class_from_string(middleware_config["class_path"])
            config_class = middleware_class.__annotations__.get(
                "config", BaseMiddlewareConfig
            )
            correct_config = config_class(**middleware_config)
            middleware = middleware_class(correct_config)
            self.middlewares.append(middleware)

    def _setup_sentry(self):
        if not self.config.sentry_dsn:
            return

        sentry_sdk.init(
            dsn=self.config.sentry_dsn,
            release=importlib.metadata.distribution("vumi2").version,
        )

    def _setup_http(self, http_bind: str) -> None:
        self.http = WorkerHttp(http_bind, self.config.worker_concurrency)
        self.resources_to_close.append(self.http)
        self.http.start(self.nursery)
        self.http.app.add_url_rule("/health", view_func=self._healthcheck_request)

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
        async with trio.open_nursery() as nursery:
            for resource in self.resources_to_close:
                nursery.start_soon(resource.aclose)
        # The nursery will block until all resources are closed.
        self._closed.set()

    async def setup(self):
        for middleware in self.middlewares:
            await middleware.setup()

    def middleware_outbound_handler(
        self, connector_name, middlewares, outbound_handler
    ):
        middlewares = [m for m in middlewares if m.outbound_enabled(connector_name)]

        @wraps(outbound_handler)
        async def _outbound_handler(msg):
            for middleware in middlewares:
                msg = await middleware.handle_outbound(msg, connector_name)
            return await outbound_handler(msg)

        return _outbound_handler

    def middleware_inbound_handler(self, connector_name, middlewares, inbound_handler):
        middlewares = [m for m in middlewares if m.inbound_enabled(connector_name)]

        @wraps(inbound_handler)
        async def _inbound_handler(msg):
            for middleware in middlewares:
                msg = await middleware.handle_inbound(msg, connector_name)
            return await inbound_handler(msg)

        return _inbound_handler

    def middleware_event_handler(self, connector_name, middlewares, event_handler):
        middlewares = [m for m in middlewares if m.event_enabled(connector_name)]

        @wraps(event_handler)
        async def _event_handler(msg):
            for middleware in middlewares:
                msg = await middleware.handle_event(msg, connector_name)
            return await event_handler(msg)

        return _event_handler

    async def start_consuming(self):
        await self._connectors.start_consuming()

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
        _inbound_handler = self.middleware_inbound_handler(
            connector_name, self.middlewares, inbound_handler
        )
        _event_handler = self.middleware_event_handler(
            connector_name, self.middlewares, event_handler
        )
        connector = ReceiveInboundConnector(
            self.nursery,
            self.connection,
            connector_name,
            self.config.worker_concurrency,
        )
        self._connectors.add(connector)
        await connector.setup(
            inbound_handler=_inbound_handler, event_handler=_event_handler
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
        _outbound_handler = self.middleware_outbound_handler(
            connector_name, self.middlewares, outbound_handler
        )

        connector = ReceiveOutboundConnector(
            self.nursery,
            self.connection,
            connector_name,
            self.config.worker_concurrency,
        )
        self._connectors.add(connector)
        await connector.setup(outbound_handler=_outbound_handler)
        self.receive_outbound_connectors[connector_name] = connector
        return connector
