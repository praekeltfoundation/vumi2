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

    def inbound_enabled(self, connector_name):
        if (
            self.config.inbound_enabled
            and connector_name in self.config.enable_for_connectors
        ):
            return True
        return False

    def outbound_enabled(self, connector_name):
        if (
            self.config.outbound_enabled
            and connector_name in self.config.enable_for_connectors
        ):
            return True
        return False

    def event_enabled(self, connector_name):
        if (
            self.config.event_enabled
            and connector_name in self.config.enable_for_connectors
        ):
            return True
        return False

    async def handle_inbound(self, message, connector_name):
        return message

    async def handle_outbound(self, message, connector_name):
        return message

    async def handle_event(self, message, connector_name):
        return message
