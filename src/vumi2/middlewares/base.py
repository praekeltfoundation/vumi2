from attr import define


@define
class BaseMiddlewareConfig:
    class_path: str
    enable_for_connectors: list[str]
    inbound_enabled: bool = False
    outbound_enabled: bool = False
    event_enabled: bool = False


class BaseMiddleware:
    config: BaseMiddlewareConfig

    def __init__(self, config):
        self.config = config

    async def setup(self):
        pass

    async def teardown_middleware(self):
        pass

    def _conn_enabled(self, connector_name):
        return connector_name in self.config.enable_for_connectors

    def inbound_enabled(self, connector_name):
        return self.config.inbound_enabled and self._conn_enabled(connector_name)

    def outbound_enabled(self, connector_name):
        return self.config.outbound_enabled and self._conn_enabled(connector_name)

    def event_enabled(self, connector_name):
        return self.config.event_enabled and self._conn_enabled(connector_name)

    async def handle_inbound(self, message, connector_name):
        return message

    async def handle_outbound(self, message, connector_name):
        return message

    async def handle_event(self, message, connector_name):
        return message
