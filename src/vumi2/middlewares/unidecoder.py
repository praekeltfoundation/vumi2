from unidecode import unidecode

from vumi2.middlewares.base import BaseMiddleware, BaseMiddlewareConfig


class Unidecoder(BaseMiddleware):
    config: BaseMiddlewareConfig

    async def setup(self):
        pass

    async def handle_inbound(self, message, connection):
        if message.content:
            message.content = unidecode(message.content)
            print(f"unidecoded message {message.content}")
        return message

    async def handle_outbound(self, message, connection):
        if message.content:
            message.content = unidecode(message.content)
            print(f"unidecoded message {message.content}")
        return message
