from typing import AsyncContextManager

import async_amqp

from vumi2.config import BaseConfig


def create_amqp_client(
    config: BaseConfig,
) -> AsyncContextManager[async_amqp.AmqpProtocol]:
    if config.amqp_url:
        client = async_amqp.connect_from_url(config.amqp_url)
    else:
        client = async_amqp.connect_amqp(
            host=config.amqp.hostname,
            port=config.amqp.port,
            login=config.amqp.username,
            password=config.amqp.password,
            virtualhost=config.amqp.vhost,
        )
    return client
