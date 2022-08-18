from typing import Dict, List, Tuple

from async_amqp import AmqpProtocol
from attrs import Factory, define
from quart import request
from quart.datastructures import Headers
from trio import Event as TrioEvent
from trio import Nursery
from werkzeug.datastructures import MultiDict

from vumi2.messages import Event, EventType, Message, generate_message_id
from vumi2.workers import BaseConfig, BaseWorker


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
    data: str
    event: TrioEvent = Factory(TrioEvent)


@define
class Response:
    data: str
    code: int
    headers: Dict[str, str]


class HttpRpcTransport(BaseWorker):
    CONFIG_CLASS = HttpRpcConfig

    # So that the type checker knows the type of self.config
    def __init__(
        self,
        nursery: Nursery,
        amqp_connection: AmqpProtocol,
        config: HttpRpcConfig,
    ) -> None:
        super().__init__(nursery, amqp_connection, config)
        self.config: HttpRpcConfig = config

    async def setup(self) -> None:
        self.requests: Dict[str, Request] = {}
        self.results: Dict[str, Response] = {}
        self.connector = await self.setup_receive_outbound_connector(
            self.config.transport_name, self.handle_outbound_message
        )
        self.http_app.add_url_rule(self.config.web_path, view_func=self.inbound_request)

    async def inbound_request(self) -> Tuple[str, int, Dict[str, str]]:
        message_id = generate_message_id()
        data = await request.get_data(as_text=True)
        r = self.requests[message_id] = Request(
            method=request.method, headers=request.headers, args=request.args, data=data
        )

        await self.handle_raw_inbound_message(message_id, r)

        # Wait for finish_request to be called
        await r.event.wait()
        response = self.results.pop(message_id)
        return response.data, response.code, response.headers

    async def handle_raw_inbound_message(
        self, message_id: str, r: Request
    ) -> None:  # pragma: no cover
        raise NotImplementedError(
            "Subclasses should implement handle_raw_inbound_message"
        )

    def ensure_message_fields(
        self, message: Message, expected_fields: List[str]
    ) -> List[str]:
        missing_fields = []
        for field in expected_fields:
            if not getattr(message, field):
                missing_fields.append(field)
        return missing_fields

    async def publish_nack(self, message_id: str, reason: str) -> None:
        await self.connector.publish_event(
            Event(
                user_message_id=message_id,
                sent_message_id=message_id,
                event_type=EventType.NACK,
                nack_reason=reason,
            )
        )

    async def publish_ack(self, message_id: str) -> None:
        await self.connector.publish_event(
            Event(
                user_message_id=message_id,
                sent_message_id=message_id,
                event_type=EventType.ACK,
            )
        )

    async def handle_outbound_message(self, message: Message) -> None:
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

    def finish_request(self, request_id: str, data: str, code=200, headers={}) -> None:
        self.results[request_id] = Response(data=data, code=code, headers=headers)
        request = self.requests.pop(request_id)
        request.event.set()
