from http import HTTPStatus


# FIXME: Do we need this base class?
class ApiError(Exception):
    name = "ApiError"
    description = "Generic Error"
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


class TimeoutError(ApiUsageError):
    name = "TimeoutError"
    description = "timeout"


class SignatureMismatchError(ApiError):
    name = "SignatureMismatchError"
    description = "Authentication failed: Invalid HMAC signature"
    status = HTTPStatus.UNAUTHORIZED


class HttpErrorResponse(ApiError):
    name = "HttpErrorResponse"
    description = "HTTP error response"
    code: int

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"HTTP error response: {code}")
