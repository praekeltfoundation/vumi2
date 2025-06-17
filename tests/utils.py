import re

from twisted.python import log


class LogCatcher:
    """Context manager for gathering logs in tests.

    :param str system:
        Only log events whose 'system' value contains the given
        regular expression pattern will be gathered. Default: None
        (i.e. keep all log events).

    :param str message:
        Only log events whose message contains the given regular
        expression pattern will be gathered. The message is
        constructed by joining the elements in the 'message' value
        with a space (the same way Twisted does). Default: None
        (i.e. keep all log events).

    :param int log_level:
        Only log events whose logLevel is equal to the given level
        will be gathered. Default: None (i.e. keep all log events).
    """

    def __init__(self, system=None, message=None, log_level=None):
        self.logs = []
        self.system = re.compile(system) if system is not None else None
        self.message = re.compile(message) if message is not None else None
        self.log_level = log_level

    @property
    def errors(self):
        return [ev for ev in self.logs if ev["isError"]]

    def messages(self):
        return [" ".join(msg["message"]) for msg in self.logs if not msg["isError"]]

    def _keep_log(self, event_dict):
        if self.system is not None:
            if not self.system.search(event_dict.get("system", "-")):
                return False
        if self.message is not None:
            log_message = " ".join(event_dict.get("message", []))
            if not self.message.search(log_message):
                return False
        if self.log_level is not None:
            if event_dict.get("logLevel", None) != self.log_level:
                return False
        return True

    def _gather_logs(self, event_dict):
        if self._keep_log(event_dict):
            self.logs.append(event_dict)

    def __enter__(self):
        log.theLogPublisher.addObserver(self._gather_logs)
        return self

    def __exit__(self, *exc_info):
        log.theLogPublisher.removeObserver(self._gather_logs)
