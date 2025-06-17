from confmodel.fields import ConfigText, ConfigBool, ConfigList

from vumi2.middlewares import BaseMiddleware
from vumi2.middlewares.base import BaseMiddlewareConfig
from vumi2 import log


class LoggingMiddlewareConfig(BaseMiddlewareConfig):
    """
    Configuration class for the logging middleware.
    """

    log_level = ConfigText(
        "Log level from :mod:`vumi.log` to log inbound and outbound messages "
        "and events at",
        default="info",
        static=True,
    )
    failure_log_level = ConfigText(
        "Log level from :mod:`vumi.log` to log failure messages at",
        default="error",
        static=True,
    )

    inbound_enabled = ConfigBool(default=True)

    outbound_enabled = ConfigBool(default=True)

    event_enabled = ConfigBool(default=True)

    allowed_connectors = ConfigList(default=[])


class LoggingMiddleware(BaseMiddleware):
    CONFIG_CLASS = LoggingMiddlewareConfig

    def setup_middleware(self):
        log_level = self.config.log_level
        self.message_logger = getattr(log, log_level)
        failure_log_level = self.config.failure_log_level
        self.failure_logger = getattr(log, failure_log_level)

    def _log(self, direction, logger, msg, connector_name):
        logger(
            "Processed %s message for %s: %s"
            % (direction, connector_name, msg.to_json())
        )
        return msg

    def allowed_connections(self, connector_name):
        return (
            not self.config.allowed_connectors
            or connector_name in self.config.allowed_connectors
        )

    def handle_inbound(self, message, connector_name):
        return self._log("inbound", self.message_logger, message, connector_name)

    def handle_outbound(self, message, connector_name):
        return self._log("outbound", self.message_logger, message, connector_name)

    def handle_event(self, event, connector_name):
        return self._log("event", self.message_logger, event, connector_name)

    def inbound_enabled(self, connector_name):
        if self.config.inbound_enabled and self.allowed_connections(connector_name):
            return True
        return False

    def outbound_enabled(self, connector_name):
        if self.config.outbound_enabled and self.allowed_connections(connector_name):
            return True
        return False

    def event_enabled(self, connector_name):
        if self.config.inbound_enabled and self.allowed_connections(connector_name):
            return True
        return False
