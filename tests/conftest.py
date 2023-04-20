import pytest

from vumi2.amqp import create_amqp_client
from vumi2.config import load_config


@pytest.fixture(scope="function")
async def amqp_connection():
    config = load_config()
    async with create_amqp_client(config) as amqp_connection:
        yield amqp_connection
