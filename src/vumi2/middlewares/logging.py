from attr import define
from vumi2.middlewares.base import BaseMiddleware, BaseMiddlewareConfig
from logging import getLogger, getLevelNamesMapping


@define
class LoggingMiddlewareConfig(BaseMiddlewareConfig):
    # TODO: vallidate log level
    log_level: str = "info"
    logger_name: str = __name__


class LoggingMiddleware(BaseMiddleware):
    config: LoggingMiddlewareConfig

    async def setup(self):
        self.log_level = getLevelNamesMapping()[self.config.log_level.upper()]
        self.logger = getLogger(self.config.logger_name)

    def _log_msg(self, direction, msg, connector_name):
        self.logger.log(
            self.log_level, f"Processed {direction} message for {connector_name}: {msg}"
        )
        return msg

    async def handle_inbound(self, message, connector_name):
        return self._log_msg("inbound", message, connector_name)

    async def handle_outbound(self, message, connector_name):
        return self._log_msg("outbound", message, connector_name)

    async def handle_event(self, event, connector_name):
        return self._log_msg("event", event, connector_name)
