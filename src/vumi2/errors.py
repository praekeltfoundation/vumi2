class VumiError(Exception):
    """
    Base class for all Vumi exceptions
    """


class DuplicateConnectorError(ValueError):
    """
    When adding a connector to a worker that has a connector with the same name
    """


class InvalidWorkerClass(VumiError):
    """
    TODO
    """
