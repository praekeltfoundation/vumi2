from logging import getLogger

from attrs import Factory, define
from quart import request
from quart.datastructures import Headers
from trio import Event as TrioEvent
from trio import move_on_after
from werkzeug.datastructures import MultiDict

from vumi2.messages import Event, EventType, Message, generate_message_id
from vumi2.workers import BaseConfig, BaseWorker

logger = getLogger(__name__)


@define
class HttpRpcConfig(BaseConfig):
    transport_name: str = "http_rpc"
    web_path: str = "/http_rpc"
    request_timeout: int = 4 * 60


@define
class Request:
    method: str
    headers: Headers
    args: MultiDict
    data: str | bytes
    event: TrioEvent = Factory(TrioEvent)


@define
class Response:
    data: str | dict
    code: int
    headers: dict[str, str]


class HttpRpcTransport(BaseWorker):
    config: HttpRpcConfig

    async def setup(self) -> None:
        await super().setup()
        self.requests: dict[str, Request] = {}
        self.results: dict[str, Response] = {}
        self.connector = await self.setup_receive_outbound_connector(
            self.config.transport_name, self.handle_outbound_message
        )
        self.http.app.add_url_rule(self.config.web_path, view_func=self.inbound_request)
        await self.start_consuming()

    async def inbound_request(self) -> tuple[str | dict, int, dict[str, str]]:
        message_id = generate_message_id()
        try:
            with move_on_after(self.config.request_timeout):
                data = await request.get_data(as_text=True)
                r = self.requests[message_id] = Request(
                    method=request.method,
                    headers=request.headers,
                    args=request.args,
                    data=data,
                )
                logger.debug("Received request %s %s", message_id, r)

                await self.handle_raw_inbound_message(message_id, r)

                # Wait for finish_request to be called
                await r.event.wait()
                response = self.results.pop(message_id)
                return response.data, response.code, response.headers
        finally:
            # Clean up any unprocessed requests or results to not leak memory
            self.requests.pop(message_id, None)
            self.results.pop(message_id, None)
        logger.warning(
            "Timing out request %s %s %s %s",
            message_id,
            request.method,
            request.url,
            request.headers,
        )
        return "", 504, {}

    async def handle_raw_inbound_message(
        self, message_id: str, r: Request
    ) -> None:  # pragma: no cover
        raise NotImplementedError(
            "Subclasses should implement handle_raw_inbound_message"
        )

    def ensure_message_fields(
        self, message: Message, expected_fields: list[str]
    ) -> list[str]:
        missing_fields = []
        for field in expected_fields:
            if not getattr(message, field):
                missing_fields.append(field)
        return missing_fields

    async def publish_nack(self, message_id: str, reason: str) -> None:
        nack = Event(
            user_message_id=message_id,
            sent_message_id=message_id,
            event_type=EventType.NACK,
            nack_reason=reason,
        )
        logger.debug("Not acknowledging message %s", nack)
        await self.connector.publish_event(nack)

    async def publish_ack(self, message_id: str) -> None:
        ack = Event(
            user_message_id=message_id,
            sent_message_id=message_id,
            event_type=EventType.ACK,
        )
        logger.debug("Acknowledging message %s", ack)
        await self.connector.publish_event(ack)

    async def handle_outbound_message(self, message: Message) -> None:
        logger.debug("Consuming outbound message %s", message)
        # Need to do this double check for the type checker
        if not message.in_reply_to or not message.content:
            missing_fields = self.ensure_message_fields(
                message, ["in_reply_to", "content"]
            )
            await self.publish_nack(
                message.message_id,
                f"Missing fields: {', '.join(missing_fields)}",
            )
            return
        if message.in_reply_to not in self.requests:
            await self.publish_nack(message.message_id, "No matching request")
            return
        self.finish_request(message.in_reply_to, message.content)
        await self.publish_ack(message.message_id)

    def finish_request(
        self, request_id: str, data: str | dict, code=200, headers=None
    ) -> None:
        headers = {} if headers is None else headers
        logger.debug(
            "Finishing request %s with %s %s %s", request_id, code, headers, data
        )
        self.results[request_id] = Response(data=data, code=code, headers=headers)
        request = self.requests.pop(request_id)
        request.event.set()
