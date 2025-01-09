from http import HTTPStatus


# FIXME: Do we need this base class?
class ApiError(Exception):
    name = "JunebugError"
    description = "Generic Junebug Error"
    status = HTTPStatus.INTERNAL_SERVER_ERROR


class ApiUsageError(ApiError):
    name = "ApiUsageError"
    description = "api usage error"
    status = HTTPStatus.BAD_REQUEST


class JsonDecodeError(ApiUsageError):
    name = "JsonDecodeError"
    description = "json decode error"


class MessageNotFound(ApiUsageError):
    name = "MessageNotFound"
    description = "message not found"


class InvalidBody(ApiUsageError):
    name = "invalid_body"
