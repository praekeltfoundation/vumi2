import xml.etree.ElementTree as ET
from logging import getLogger
from urllib.parse import urlencode, urljoin

from async_amqp import AmqpProtocol
from attrs import define
from trio import Nursery

from vumi2.messages import AddressType, Message, Session, TransportType
from vumi2.transports.httprpc import HttpRpcConfig, HttpRpcTransport, Request

logger = getLogger(__name__)


@define
class AatUssdTransportConfig(HttpRpcConfig):
    base_url: str = "http://localhost"


class AatUssdTransport(HttpRpcTransport):
    CONFIG_CLASS = AatUssdTransportConfig
    EXPECTED_FIELDS = {"msisdn", "provider"}

    # So that the type checker knows the type of self.config
    def __init__(
        self,
        nursery: Nursery,
        amqp_connection: AmqpProtocol,
        config: AatUssdTransportConfig,
    ) -> None:
        super().__init__(nursery, amqp_connection, config)
        self.config: AatUssdTransportConfig = config

    async def handle_raw_inbound_message(self, message_id: str, r: Request) -> None:
        values = r.args
        missing_fields = self.EXPECTED_FIELDS - set(values.keys())
        # One of to_addr or request is required
        if values.get("to_addr") is None and values.get("request") is None:
            missing_fields.add("request")
        if missing_fields:
            logger.info("Invalid request, missing fields %s", missing_fields)
            self.finish_request(
                request_id=message_id,
                data={"missing_parameter": sorted(missing_fields)},
                code=400,
            )
            return

        from_addr = values["msisdn"]
        provider = values["provider"]
        ussd_session_id = values.get("ussdSessionId")

        if values.get("to_addr") is not None:
            session_event = Session.RESUME
            to_addr = values["to_addr"]
            content = values.get("request")
        else:
            session_event = Session.NEW
            to_addr = values["request"]
            content = None

        message = Message(
            to_addr=to_addr,
            from_addr=from_addr,
            transport_name=self.config.transport_name,
            transport_type=TransportType.USSD,
            message_id=message_id,
            session_event=session_event,
            content=content,
            from_addr_type=AddressType.MSISDN,
            helper_metadata={"session_id": ussd_session_id},
            transport_metadata={
                "aat_ussd": {
                    "provider": provider,
                    "ussd_session_id": ussd_session_id,
                }
            },
            provider=provider,
        )
        logger.debug("Publishing inbound message %s", message)
        await self.connector.publish_inbound(message)

    def generate_body(self, reply: str, callback: str, session_event: Session) -> str:
        request = ET.Element("request")
        headertext = ET.SubElement(request, "headertext")
        headertext.text = reply

        if session_event != Session.CLOSE:
            options = ET.SubElement(request, "options")
            ET.SubElement(
                options,
                "option",
                {
                    "command": "1",
                    "order": "1",
                    "callback": callback,
                    "display": "false",
                },
            )

        return ET.tostring(request, encoding="utf-8")

    def get_callback_url(self, to_addr: str):
        url = urljoin(self.config.base_url, self.config.web_path)
        query = urlencode({"to_addr": to_addr})
        return f"{url}?{query}"

    async def handle_outbound_message(self, message: Message) -> None:
        logger.debug("Consuming outbound message %s", message)
        if not message.in_reply_to:
            logger.info("Outbound message is not a reply, will nack")
            await self.publish_nack(
                message.message_id, "Outbound message is not a reply"
            )
            return
        if not message.content:
            logger.info("Outbound message has no content, will nack")
            await self.publish_nack(
                message.message_id, "Outbound message has no content"
            )
            return

        callback_url = self.get_callback_url(message.from_addr)
        body = self.generate_body(message.content, callback_url, message.session_event)
        self.finish_request(message.in_reply_to, body)
        await self.publish_ack(message.message_id)
